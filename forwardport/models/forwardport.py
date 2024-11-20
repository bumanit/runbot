# -*- coding: utf-8 -*-
import builtins
import logging
import re
from contextlib import ExitStack
from datetime import datetime, timedelta

import requests
import sentry_sdk
from babel.dates import format_timedelta
from dateutil import relativedelta

from odoo import api, fields, models
from odoo.addons.runbot_merge import git
from odoo.addons.runbot_merge.github import GH

# how long a merged PR survives
MERGE_AGE = relativedelta.relativedelta(weeks=2)
FOOTER = '\nMore info at https://github.com/odoo/odoo/wiki/Mergebot#forward-port\n'

_logger = logging.getLogger(__name__)

class Queue:
    __slots__ = ()
    limit = 100

    def _process_item(self):
        raise NotImplementedError

    def _process(self):
        skip = 0
        from_clause, where_clause, params = self._search(self._search_domain(), order='create_date, id', limit=1).get_sql()
        for _ in range(self.limit):
            self.env.cr.execute(f"""
                SELECT id FROM {from_clause}
                WHERE {where_clause or "true"}
                ORDER BY create_date, id
                LIMIT 1 OFFSET %s
                FOR UPDATE SKIP LOCKED
            """, [*params, skip])
            b = self.browse(self.env.cr.fetchone())
            if not b:
                return

            try:
                with sentry_sdk.start_span(description=self._name):
                    b._process_item()
                b.unlink()
                self.env.cr.commit()
            except Exception:
                _logger.exception("Error while processing %s, skipping", b)
                self.env.cr.rollback()
                if b._on_failure():
                    skip += 1
                self.env.cr.commit()

    def _on_failure(self):
        return True

    def _search_domain(self):
        return []

class ForwardPortTasks(models.Model, Queue):
    _name = 'forwardport.batches'
    _description = 'batches which got merged and are candidates for forward-porting'

    limit = 10

    batch_id = fields.Many2one('runbot_merge.batch', required=True, index=True)
    source = fields.Selection([
        ('merge', 'Merge'),
        ('fp', 'Forward Port Followup'),
        ('insert', 'New branch port'),
        ('complete', 'Complete ported batches'),
    ], required=True)
    retry_after = fields.Datetime(required=True, default='1900-01-01 01:01:01')
    retry_after_relative = fields.Char(compute="_compute_retry_after_relative")
    pr_id = fields.Many2one('runbot_merge.pull_requests')

    @api.model_create_multi
    def create(self, vals_list):
        self.env.ref('forwardport.port_forward')._trigger()
        return super().create(vals_list)

    def write(self, vals):
        if retry := vals.get('retry_after'):
            self.env.ref('forwardport.port_forward')\
                ._trigger(fields.Datetime.to_datetime(retry))
        return super().write(vals)

    def _search_domain(self):
        return super()._search_domain() + [
            ('retry_after', '<=', fields.Datetime.to_string(fields.Datetime.now())),
        ]

    @api.depends('retry_after')
    def _compute_retry_after_relative(self):
        now = fields.Datetime.now()
        for t in self:
            if t.retry_after <= now:
                t.retry_after_relative = ""
            else:
                t.retry_after_relative = format_timedelta(t.retry_after - now, locale=t.env.lang)

    def _on_failure(self):
        super()._on_failure()
        self.retry_after = fields.Datetime.to_string(fields.Datetime.now() + timedelta(minutes=30))

    def _process_item(self):
        batch = self.batch_id
        sentry_sdk.set_tag('forward-porting', batch.prs.mapped('display_name'))
        if self.source == 'complete':
            self._complete_batches()
            return

        newbatch = batch._port_forward()
        if not newbatch:  # reached end of seq (or batch is empty)
            # FIXME: or configuration is fucky so doesn't want to FP (maybe should error and retry?)
            _logger.info(
                "Processed %s from %s (%s) -> end of the sequence",
                batch, self.source, batch.prs.mapped('display_name'),
            )
            return

        _logger.info(
            "Processed %s from %s (%s) -> %s (%s)",
            batch, self.source, ', '.join(batch.prs.mapped('display_name')),
            newbatch, ', '.join(newbatch.prs.mapped('display_name')),
        )
        # insert new batch in ancestry sequence
        if self.source == 'insert':
            self._process_insert(batch, newbatch)

    def _process_insert(self, batch, newbatch):
        self.env['runbot_merge.batch'].search([
            ('parent_id', '=', batch.id),
            ('id', '!=', newbatch.id),
        ]).parent_id = newbatch.id
        # insert new PRs in ancestry sequence unless conflict (= no parent)
        for pr in newbatch.prs:
            next_target = pr._find_next_target()
            if not next_target:
                continue

            # should have one since it was inserted before an other PR?
            descendant = pr.search([
                ('target', '=', next_target.id),
                ('source_id', '=', pr.source_id.id),
            ])

            # copy the reviewing of the "descendant" (even if detached) to this pr
            if reviewer := descendant.reviewed_by:
                pr.reviewed_by = reviewer

            # replace parent_id *if not detached*
            if descendant.parent_id:
                descendant.parent_id = pr.id

    def _complete_batches(self):
        source = pr = self.pr_id
        if not pr:
            _logger.warning(
                "Unable to complete descendants of %s (%s): no new PR",
                self.batch_id,
                self.batch_id.prs.mapped('display_name'),
            )
            return
        _logger.info(
            "Completing batches for descendants of %s (added %s)",
            self.batch_id.prs.mapped('display_name'),
            self.pr_id.display_name,
        )

        gh = requests.Session()
        repository = pr.repository
        gh.headers['Authorization'] = f'token {repository.project_id.fp_github_token}'
        PullRequests = self.env['runbot_merge.pull_requests']
        self.env.cr.execute('LOCK runbot_merge_pull_requests IN SHARE MODE')

        # TODO: extract complete list of targets from `_find_next_target`
        #       so we can create all the forwardport branches, push them, and
        #       only then create the PR objects
        # TODO: maybe do that after making forward-port WC-less, so all the
        #       branches can be pushed atomically at once
        for descendant in self.batch_id.descendants():
            target = pr._find_next_target()
            if target is None:
                _logger.info("Will not forward-port %s: no next target", pr.display_name)
                return

            if PullRequests.search_count([
                ('source_id', '=', source.id),
                ('target', '=', target.id),
                ('state', 'not in', ('closed', 'merged')),
            ]):
                _logger.warning("Will not forward-port %s: already ported", pr.display_name)
                return

            if target != descendant.target:
                self.env['runbot_merge.pull_requests.feedback'].create({
                    'repository': repository.id,
                    'pull_request': source.id,
                    'token_field': 'fp_github_token',
                    'message': """\
{pr.ping}unable to port this PR forwards due to inconsistency: goes from \
{pr.target.name} to {next_target.name} but {batch} ({batch_prs}) targets \
{batch.target.name}.
""".format(pr=pr, next_target=target, batch=descendant, batch_prs=', '.join(descendant.mapped('prs.display_name')))
                })
                return

            ref = descendant.prs[:1].refname
            # NOTE: ports the new source everywhere instead of porting each
            #       PR to the next step as it does not *stop* on conflict
            repo = git.get_local(source.repository)
            conflict, head = source._create_port_branch(repo, target, forward=True)
            repo.push(git.fw_url(pr.repository), f'{head}:refs/heads/{ref}')

            remote_target = repository.fp_remote_target
            owner, _ = remote_target.split('/', 1)
            message = source.message + f"\n\nForward-Port-Of: {pr.display_name}"

            title, body = re.match(r'(?P<title>[^\n]+)\n*(?P<body>.*)', message, flags=re.DOTALL).groups()
            r = gh.post(f'https://api.github.com/repos/{pr.repository.name}/pulls', json={
                'base': target.name,
                'head': f'{owner}:{ref}',
                'title': '[FW]' + (' ' if title[0] != '[' else '') + title,
                'body': body
            })
            if not r.ok:
                _logger.warning("Failed to create forward-port PR for %s, deleting branches", pr.display_name)
                # delete all the branches this should automatically close the
                # PRs if we've created any. Using the API here is probably
                # simpler than going through the working copies
                d = gh.delete(f'https://api.github.com/repos/{remote_target}/git/refs/heads/{ref}')
                if d.ok:
                    _logger.info("Deleting %s:%s=success", remote_target, ref)
                else:
                    _logger.warning("Deleting %s:%s=%s", remote_target, ref, d.text)
                raise RuntimeError(f"Forwardport failure: {pr.display_name} ({r.text})")

            new_pr = PullRequests._from_gh(r.json())
            _logger.info("Created forward-port PR %s", new_pr)
            new_pr.write({
                'batch_id': descendant.id, # should already be set correctly but...
                'merge_method': pr.merge_method,
                'source_id': source.id,
                # only link to previous PR of sequence if cherrypick passed
                # FIXME: apply parenting of siblings? Apply parenting *to* siblings?
                'parent_id': pr.id if not conflict else False,
                'detach_reason': "{1}\n{2}".format(*conflict).strip() if conflict else None,
            })

            if conflict:
                self.env.ref('runbot_merge.forwardport.failure.conflict')._send(
                    repository=pr.repository,
                    pull_request=pr.number,
                    token_field='fp_github_token',
                    format_args={'source': source, 'pr': pr, 'new': new_pr, 'footer': FOOTER},
                )
            new_pr._fp_conflict_feedback(pr, {pr: conflict})

            labels = ['forwardport']
            if conflict:
                labels.append('conflict')
            self.env['runbot_merge.pull_requests.tagging'].create({
                'repository': new_pr.repository.id,
                'pull_request': new_pr.number,
                'tags_add': labels,
            })

            pr = new_pr

class UpdateQueue(models.Model, Queue):
    _name = 'forwardport.updates'
    _description = 'if a forward-port PR gets updated & has followups (cherrypick succeeded) the followups need to be updated as well'

    limit = 10

    original_root = fields.Many2one('runbot_merge.pull_requests')
    new_root = fields.Many2one('runbot_merge.pull_requests')

    @api.model_create_multi
    def create(self, vals_list):
        self.env.ref('forwardport.updates')._trigger()
        return super().create(vals_list)

    def _process_item(self):
        previous = self.new_root
        sentry_sdk.set_tag("update-root", self.new_root.display_name)
        with ExitStack() as s:
            for child in self.new_root._iter_descendants():
                self.env.cr.execute("""
                    SELECT id
                    FROM runbot_merge_pull_requests
                    WHERE id = %s
                    FOR UPDATE NOWAIT
                """, [child.id])
                _logger.info(
                    "Re-port %s from %s (changed root %s -> %s)",
                    child.display_name,
                    previous.display_name,
                    self.original_root.display_name,
                    self.new_root.display_name
                )
                if child.state in ('closed', 'merged'):
                    self.env.ref('runbot_merge.forwardport.updates.closed')._send(
                        repository=child.repository,
                        pull_request=child.number,
                        token_field='fp_github_token',
                        format_args={'pr': child, 'parent': self.new_root},
                    )
                    return

                repo = git.get_local(previous.repository)
                conflicts, new_head = previous._create_port_branch(repo, child.target, forward=True)

                if conflicts:
                    _, out, err, _ = conflicts
                    self.env.ref('runbot_merge.forwardport.updates.conflict.parent')._send(
                        repository=previous.repository,
                        pull_request=previous.number,
                        token_field='fp_github_token',
                        format_args={'pr': previous, 'next': child},
                    )
                    self.env.ref('runbot_merge.forwardport.updates.conflict.child')._send(
                        repository=child.repository,
                        pull_request=child.number,
                        token_field='fp_github_token',
                        format_args={
                            'previous': previous,
                            'pr': child,
                            'stdout': (f'\n\nstdout:\n```\n{out.strip()}\n```' if out.strip() else ''),
                            'stderr': (f'\n\nstderr:\n```\n{err.strip()}\n```' if err.strip() else ''),
                        },
                    )

                commits_count = int(repo.stdout().rev_list(
                    f'{child.target.name}..{new_head}',
                    count=True
                ).stdout.decode().strip())
                old_head = child.head
                # update child's head to the head we're going to push
                child.with_context(ignore_head_update=True).write({
                    'head': new_head,
                    # 'state': 'opened',
                    'squash': commits_count == 1,
                })
                # then update the child's branch to the new head
                repo.push(
                    f'--force-with-lease={child.refname}:{old_head}',
                    git.fw_url(child.repository),
                    f"{new_head}:refs/heads/{child.refname}")

                # committing here means github could technically trigger its
                # webhook before sending a response, but committing before
                # would mean we can update the PR in database but fail to
                # update on github, which is probably worse?
                # alternatively we can commit, push, and rollback if the push
                # fails
                # FIXME: handle failures (especially on non-first update)
                self.env.cr.commit()

                previous = child

_deleter = _logger.getChild('deleter')
class DeleteBranches(models.Model, Queue):
    _name = 'forwardport.branch_remover'
    _description = "Removes branches of merged PRs"

    pr_id = fields.Many2one('runbot_merge.pull_requests')

    @api.model_create_multi
    def create(self, vals_list):
        self.env.ref('forwardport.remover')._trigger(datetime.now() - MERGE_AGE)
        return super().create(vals_list)

    def _search_domain(self):
        cutoff = getattr(builtins, 'forwardport_merged_before', None) \
             or fields.Datetime.to_string(datetime.now() - MERGE_AGE)
        return [('pr_id.merge_date', '<', cutoff)]

    def _process_item(self):
        _deleter.info(
            "PR %s: checking deletion of linked branch %s",
            self.pr_id.display_name,
            self.pr_id.label
        )

        if self.pr_id.state != 'merged':
            _deleter.info('✘ PR is not "merged" (got %s)', self.pr_id.state)
            return

        repository = self.pr_id.repository
        fp_remote = repository.fp_remote_target
        if not fp_remote:
            _deleter.info('✘ no forward-port target')
            return

        repo_owner, repo_name = fp_remote.split('/')
        owner, branch = self.pr_id.label.split(':')
        if repo_owner != owner:
            _deleter.info('✘ PR owner != FP target owner (%s)', repo_owner)
            return # probably don't have access to arbitrary repos

        github = GH(token=repository.project_id.fp_github_token, repo=fp_remote)
        refurl = 'git/refs/heads/' + branch
        ref = github('get', refurl, check=False)
        if ref.status_code != 200:
            _deleter.info("✘ branch already deleted (%s)", ref.json())
            return

        ref = ref.json()
        if isinstance(ref, list):
            _deleter.info(
                "✘ got a fuzzy match (%s), branch probably deleted",
                ', '.join(r['ref'] for r in ref)
            )
            return

        if ref['object']['sha'] != self.pr_id.head:
            _deleter.info(
                "✘ branch %s head mismatch, expected %s, got %s",
                self.pr_id.label,
                self.pr_id.head,
                ref['object']['sha']
            )
            return

        r = github('delete', refurl, check=False)
        assert r.status_code == 204, \
            "Tried to delete branch %s of %s, got %s" % (
                branch, self.pr_id.display_name,
                r.json()
            )
        _deleter.info('✔ deleted branch %s of PR %s', self.pr_id.label, self.pr_id.display_name)
