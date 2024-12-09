import base64
import collections
import contextlib
import dataclasses
import io
import json
import logging
import os
import pprint
import re
from collections.abc import Mapping
from difflib import Differ
from operator import itemgetter
from typing import Dict, Union, Optional, Literal, Callable, Iterator, Tuple, List, TypeAlias

from werkzeug.datastructures import Headers

from odoo import api, models, fields, Command
from odoo.tools import OrderedSet, groupby
from .pull_requests import Branch, Stagings, PullRequests, Repository
from .batch import Batch
from .. import exceptions, utils, github, git

WAIT_FOR_VISIBILITY = [10, 10, 10, 10]
_logger = logging.getLogger(__name__)


class Project(models.Model):
    _inherit = 'runbot_merge.project'


@dataclasses.dataclass(slots=True)
class StagingSlice:
    """Staging state for a single repository:

    - gh is a cache for the github proxy object (contains a session for reusing
      connection)
    - head is the current staging head for the branch of that repo
    """
    gh: github.GH
    head: str
    repo: git.Repo
    tasks: set[int] = dataclasses.field(default_factory=set)


StagingState: TypeAlias = Dict[Repository, StagingSlice]

def try_staging(branch: Branch) -> Optional[Stagings]:
    """ Tries to create a staging if the current branch does not already
    have one. Returns None if the branch already has a staging or there
    is nothing to stage, the newly created staging otherwise.
    """
    _logger.info(
        "Checking %s (%s) for staging: %s, skip? %s",
        branch, branch.name,
        branch.active_staging_id,
        bool(branch.active_staging_id)
    )
    if branch.active_staging_id:
        return None

    def log(label: str, batches: Batch) -> None:
        _logger.info(label, ', '.join(batches.mapped('prs.display_name')))

    alone, batches = ready_batches(for_branch=branch)

    parent_id = False
    if alone:
        log("staging high-priority PRs %s", batches)
    elif branch.project_id.staging_priority == 'default':
        if split := branch.split_ids[:1].with_context(staging_split=True):
            parent_id = split.source_id.id
            batches = split.batch_ids
            split.unlink()
            log("staging split PRs %s (prioritising splits)", batches)
        else:
            # priority, normal; priority = sorted ahead of normal, so always picked
            # first as long as there's room
            log("staging ready PRs %s (prioritising splits)", batches)
    elif branch.project_id.staging_priority == 'ready':
        if batches:
            log("staging ready PRs %s (prioritising ready)", batches)
        else:
            split = branch.split_ids[:1].with_context(staging_split=True)
            parent_id = split.source_id.id
            batches = split.batch_ids
            split.unlink()
            log("staging split PRs %s (prioritising ready)", batches)
    else:
        assert branch.project_id.staging_priority == 'largest'
        maxsplit = max(branch.split_ids, key=lambda s: len(s.batch_ids), default=branch.env['runbot_merge.split'])
        _logger.info("largest split = %d, ready = %d", len(maxsplit.batch_ids), len(batches))
        # bias towards splits if len(ready) = len(batch_ids)
        if len(maxsplit.batch_ids) >= len(batches):
            batches = maxsplit.batch_ids
            maxsplit.unlink()
            log("staging split PRs %s (prioritising largest)", batches)
        else:
            log("staging ready PRs %s (prioritising largest)", batches)

    if not batches:
        return

    original_heads, staging_state = staging_setup(branch, batches)

    staged = stage_batches(branch, batches, staging_state)

    if not staged:
        return None

    env = branch.env
    heads = []
    commits = []
    issues = []
    for repo, it in staging_state.items():
        if it.head == original_heads[repo] and branch.project_id.uniquifier:
            # if we didn't stage anything for that repo and uniquification is
            # enabled, create a dummy commit with a uniquifier to ensure we
            # don't hit a previous version of the same to ensure the staging
            # head is new and we're building everything
            project = branch.project_id
            uniquifier = base64.b64encode(os.urandom(12)).decode('ascii')
            dummy_head = it.repo.with_config(check=True).commit_tree(
                # somewhat exceptionally, `commit-tree` wants an actual tree
                # not a tree-ish
                tree=f'{it.head}^{{tree}}',
                parents=[it.head],
                author=(project.github_name, project.github_email),
                message=f'''\
force rebuild

uniquifier: {uniquifier}
For-Commit-Id: {it.head}
''',
            ).stdout.strip()

            # see above, ideally we don't need to mark the real head as
            # `to_check` because it's an old commit but `DO UPDATE` is necessary
            # for `RETURNING` to work, and it doesn't really hurt (maybe)
            env.cr.execute(
                "INSERT INTO runbot_merge_commit (sha, to_check, statuses) "
                "VALUES (%s, false, '{}'), (%s, true, '{}') "
                "ON CONFLICT (sha) DO UPDATE SET to_check=true "
                "RETURNING id",
                [it.head, dummy_head]
            )
            ([commit], [head]) = env.cr.fetchall()
            it.head = dummy_head
        else:
            # otherwise just create a record for that commit, or flag existing
            # one as to-recheck in case there are already statuses we want to
            # propagate to the staging or something
            env.cr.execute(
                "INSERT INTO runbot_merge_commit (sha, to_check, statuses) "
                "VALUES (%s, true, '{}') "
                "ON CONFLICT (sha) DO UPDATE SET to_check=true "
                "RETURNING id",
                [it.head]
            )
            [commit] = [head] = env.cr.fetchone()

        heads.append(fields.Command.create({
            'repository_id': repo.id,
            'commit_id': head,
        }))
        commits.append(fields.Command.create({
            'repository_id': repo.id,
            'commit_id': commit,
        }))
        issues.extend(
            {'repository_id': repo.id, 'number': i}
            for i in it.tasks
        )

    # create actual staging object
    st: Stagings = env['runbot_merge.stagings'].create({
        'target': branch.id,
        'staging_batch_ids': [Command.create({'runbot_merge_batch_id': batch.id}) for batch in staged],
        'heads': heads,
        'commits': commits,
        'issues_to_close': issues,
        'parent_id': parent_id,
    })
    for repo, it in staging_state.items():
        _logger.info(
            "%s: create staging for %s:%s at %s",
            branch.project_id.name, repo.name, branch.name,
            it.head
        )
        it.repo.stdout(False).check(True).push(
            '-f',
            git.source_url(repo),
            f'{it.head}:refs/heads/staging.{branch.name}',
        )

    _logger.info("Created staging %s (%s) to %s", st, ', '.join(
        '%s[%s]' % (batch, batch.prs)
        for batch in staged
    ), st.target.name)
    return st


def ready_batches(for_branch: Branch) -> Tuple[bool, Batch]:
    env = for_branch.env
    # splits are ready by definition, we need to exclude them from the ready
    # rows otherwise if a prioritised (alone) PR is part of a split it'll be
    # staged through priority *and* through split.
    split_ids = for_branch.split_ids.batch_ids.ids
    env.cr.execute("""
        SELECT exists (
            SELECT FROM runbot_merge_batch
            WHERE priority = 'alone'
              AND blocked IS NULL
              AND target = %s
              AND NOT id = any(%s)
        )
    """, [for_branch.id, split_ids])
    [alone] = env.cr.fetchone()

    return (
        alone,
        env['runbot_merge.batch'].search([
            ('target', '=', for_branch.id),
            ('blocked', '=', False),
            ('priority', '=', 'alone') if alone else (1, '=', 1),
            ('id', 'not in', split_ids),
        ], order="priority DESC, write_date ASC, id ASC"),
    )


def staging_setup(
        target: Branch,
        batches: Batch,
) -> Tuple[Dict[Repository, str], StagingState]:
    """Sets up the staging:

    - stores baseline info
    - creates tmp branch via gh API (to remove)
    - generates working copy for each repository with the target branch
    """
    by_repo: Mapping[Repository, List[PullRequests]] = \
        dict(groupby(batches.prs, lambda p: p.repository))

    staging_state = {}
    original_heads = {}
    for repo in target.project_id.repo_ids.having_branch(target):
        gh = repo.github()
        head = gh.head(target.name)

        source = git.get_local(repo)
        source.fetch(
            git.source_url(repo),
            # a full refspec is necessary to ensure we actually fetch the ref
            # (not just the commit it points to) and update it.
            # `git fetch $remote $branch` seems to work locally, but it might
            # be hooked only to "proper" remote-tracking branches
            # (in `refs/remotes`), it doesn't seem to work here
            f'+refs/heads/{target.name}:refs/heads/{target.name}',
            *(pr.head for pr in by_repo.get(repo, [])),
            no_tags=True,
        )
        original_heads[repo] = head
        staging_state[repo] = StagingSlice(gh=gh, head=head, repo=source.stdout().with_config(text=True, check=False))

    return original_heads, staging_state


def stage_batches(branch: Branch, batches: Batch, staging_state: StagingState) -> Stagings:
    batch_limit = branch.project_id.batch_limit
    env = branch.env
    staged = env['runbot_merge.batch']
    for batch in batches:
        if len(staged) >= batch_limit:
            break
        try:
            staged |= stage_batch(env, batch, staging_state)
        except exceptions.Skip:
            # skip silently because the PR will be retried on every staging
            # which is going to be ultra spammy
            pass
        except exceptions.MergeError as e:
            pr = e.args[0]
            _logger.info("Failed to stage %s into %s", pr.display_name, branch.name)
            pr._message_log(body=f"Failed to stage into {branch.name}: {e}")
            if not staged or isinstance(e, exceptions.Unmergeable):
                if len(e.args) > 1 and e.args[1]:
                    reason = e.args[1]
                else:
                    reason = e.__cause__ or e.__context__
                # if the reason is a json document, assume it's a github error
                # and try to extract the error message to give it to the user
                with contextlib.suppress(Exception):
                    reason = json.loads(str(reason))['message'].lower()

                pr.error = True
                env.ref('runbot_merge.pr.merge.failed')._send(
                    repository=pr.repository,
                    pull_request=pr.number,
                    format_args={'pr': pr, 'reason': reason, 'exc': e},
                )
    return staged


refline = re.compile(rb'([\da-f]{40}) ([^\0\n]+)(\0.*)?\n?')
ZERO_REF = b'0'*40

def parse_refs_smart(read: Callable[[int], bytes]) -> Iterator[Tuple[str, str]]:
    """ yields pkt-line data (bytes), or None for flush lines """
    def read_line() -> Optional[bytes]:
        length = int(read(4), 16)
        if length == 0:
            return None
        return read(length - 4)

    header = read_line()
    assert header and header.rstrip() == b'# service=git-upload-pack', header
    assert read_line() is None, "failed to find first flush line"
    # read lines until second delimiter
    for line in iter(read_line, None):
        if line.startswith(ZERO_REF):
            break # empty list (no refs)
        m = refline.fullmatch(line)
        assert m
        yield m[1].decode(), m[2].decode()


UNCHECKABLE = ['merge_method', 'overrides', 'draft']


def stage_batch(env: api.Environment, batch: Batch, staging: StagingState):
    """Stages the batch represented by the ``prs`` recordset, onto the
    current corresponding staging heads.

    Alongside returning the newly created batch, updates ``staging[*].head``
    in-place on success. On failure, the heads should not be touched.

    May return an empty recordset on some non-fatal failures.
    """
    new_heads: Dict[PullRequests, str] = {}
    pr_fields = env['runbot_merge.pull_requests']._fields
    for pr in batch.prs:
        info = staging[pr.repository]
        _logger.info(
            "Staging pr %s for target %s; method=%s",
            pr.display_name, pr.target.name,
            pr.merge_method or (pr.squash and 'single') or None
        )

        try:
            method, new_heads[pr] = stage(pr, info, related_prs=(batch.prs - pr))
            _logger.info(
                "Staged pr %s to %s by %s: %s -> %s",
                pr.display_name, pr.target.name, method,
                info.head, new_heads[pr]
            )
        except github.MergeError as e:
            raise exceptions.MergeError(pr) from e
        except exceptions.Mismatch as e:
            diff = ''.join(Differ().compare(
                list(format_for_difflib((n, v) for n, v, _ in e.args[1])),
                list(format_for_difflib((n, v) for n, _, v in e.args[1])),
            ))
            _logger.info("Failed to stage %s: data mismatch", pr.display_name)
            pr._message_log(body=f"data mismatch before merge:\n{diff}")
            env.ref('runbot_merge.pr.staging.mismatch')._send(
                repository=pr.repository,
                pull_request=pr.number,
                format_args={
                    'pr': pr,
                    'mismatch': ', '.join(pr_fields[f].string for f in e.args[0]),
                    'diff': diff,
                    'unchecked': ', '.join(pr_fields[f].string for f in UNCHECKABLE)
                }
            )
            return env['runbot_merge.batch']

    # update meta to new heads
    for pr, head in new_heads.items():
        staging[pr.repository].head = head
    return batch

def format_for_difflib(items: Iterator[Tuple[str, object]]) -> Iterator[str]:
    """ Bit of a pain in the ass because difflib really wants
    all lines to be newline-terminated, but not all values are
    actual lines, and also needs to split multiline values.
    """
    for name, value in items:
        yield name + ':\n'
        value = str(value)
        if not value.endswith('\n'):
            value += '\n'
        yield from value.splitlines(keepends=True)
        yield '\n'


Method = Literal['merge', 'rebase-merge', 'rebase-ff', 'squash']
def stage(pr: PullRequests, info: StagingSlice, related_prs: PullRequests) -> Tuple[Method, str]:
    # nb: pr_commits is oldest to newest so pr.head is pr_commits[-1]
    _, prdict = info.gh.pr(pr.number)
    commits = prdict['commits']
    method: Method = pr.merge_method or ('rebase-ff' if commits == 1 else None)
    if commits > 50 and method.startswith('rebase'):
        raise exceptions.Unmergeable(pr, "Rebasing 50 commits is too much.")
    if commits > 250:
        raise exceptions.Unmergeable(
            pr, "Merging PRs of 250 or more commits is not supported "
                "(https://developer.github.com/v3/pulls/#list-commits-on-a-pull-request)"
        )
    pr_commits = info.gh.commits(pr.number)
    for c in pr_commits:
        if not (c['commit']['author']['email'] and c['commit']['committer']['email']):
            raise exceptions.Unmergeable(
                pr,
                f"All commits must have author and committer email, "
                f"missing email on {c['sha']} indicates the authorship is "
                f"most likely incorrect."
            )

    # sync and signal possibly missed updates
    invalid = {}
    diff = []
    pr_head = pr_commits[-1]['sha']
    if pr.head != pr_head:
        invalid['head'] = pr_head
        diff.append(('Head', pr.head, pr_head))

    if pr.target.name != prdict['base']['ref']:
        branch = pr.env['runbot_merge.branch'].with_context(active_test=False).search([
            ('name', '=', prdict['base']['ref']),
            ('project_id', '=', pr.repository.project_id.id),
        ])
        if not branch:
            pr.unlink()
            raise exceptions.Unmergeable(pr, "While staging, found this PR had been retargeted to an un-managed branch.")
        invalid['target'] = branch.id
        diff.append(('Target branch', pr.target.name, branch.name))

    if not method:
        if not pr.method_warned:
            pr.env.ref('runbot_merge.pr.merge_method')._send(
                repository=pr.repository,
                pull_request=pr.number,
                format_args={'pr': pr, 'methods': ''.join(
                    '* `%s` to %s\n' % pair
                    for pair in type(pr).merge_method.selection
                    if pair[0] != 'squash'
                )},
            )
            pr.method_warned = True
        raise exceptions.Skip()

    msg = utils.make_message(prdict)
    if pr.message != msg:
        invalid['message'] = msg
        diff.append(('Message', pr.message, msg))

    if invalid:
        pr.write({**invalid, 'reviewed_by': False, 'head': pr_head})
        raise exceptions.Mismatch(invalid, diff)

    if pr.reviewed_by and pr.reviewed_by.name == pr.reviewed_by.github_login:
        # XXX: find other trigger(s) to sync github name?
        gh_name = info.gh.user(pr.reviewed_by.github_login)['name']
        if gh_name:
            pr.reviewed_by.name = gh_name

    match method:
        case 'merge':
            fn = stage_merge
        case 'rebase-merge':
            fn = stage_rebase_merge
        case 'rebase-ff':
            fn = stage_rebase_ff
        case 'squash':
            fn = stage_squash

    pr_base_tree = info.repo.get_tree(pr_commits[0]['parents'][0]['sha'])
    pr_head_tree = pr_commits[-1]['commit']['tree']['sha']

    merge_base_tree = info.repo.get_tree(info.head)
    new_head = fn(pr, info, pr_commits, related_prs=related_prs)
    merge_head_tree = info.repo.get_tree(new_head)

    if pr_head_tree != pr_base_tree and merge_head_tree == merge_base_tree:
        raise exceptions.MergeError(pr, f'results in an empty tree when merged, might be the duplicate of a merged PR.')

    finder = re.compile(r"""
    \b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)
    \s+\#([0-9]+)
    """, re.VERBOSE | re.IGNORECASE)

    # TODO: maybe support closing issues in other repositories of the same project?
    info.tasks.update(
        int(m[1])
        for commit in pr_commits
        for m in finder.finditer(commit['commit']['message'])
    )
    # Turns out if the PR is not targeted at the default branch, apparently
    # github doesn't parse its description and add links it to the closing
    # issues references, it's just "fuck off". So we need to handle that one by
    # hand too.
    info.tasks.update(int(m[1]) for m in finder.finditer(pr.message))
    # So this ends up being *exclusively* for manually linked issues #feelsgoodman.
    owner, name = pr.repository.name.split('/')
    r = info.gh('post', '/graphql', json={
        'query': """
query ($owner: String!, $name: String!, $pr: Int!) {
    repository(owner: $owner, name: $name) {
        pullRequest(number: $pr) {
            closingIssuesReferences(last: 100) {
                nodes {
                    number
                    repository {
                        nameWithOwner
                    }
                }
            }
        }
    }
}
    """,
        'variables': {'owner': owner, 'name': name, 'pr': pr.number}
    })
    res = r.json()
    if 'errors' in res:
        _logger.warning(
            "Failed to fetch closing issues for %s\n%s",
            pr.display_name,
            pprint.pformat(res['errors'])
        )
    else:
        info.tasks.update(
            n['number']
            for n in res['data']['repository']['pullRequest']['closingIssuesReferences']['nodes']
            if n['repository']['nameWithOwner'] == pr.repository.name
        )
    return method, new_head

def stage_squash(pr: PullRequests, info: StagingSlice, commits: List[github.PrCommit], related_prs: PullRequests) -> str:
    msg = pr._build_message(pr, related_prs=related_prs)

    authors = {
        (c['commit']['author']['name'], c['commit']['author']['email'])
        for c in commits
    }
    if len(authors) == 1:
        author = authors.pop()
    else:
        msg.headers.extend(sorted(
            ('Co-Authored-By', "%s <%s>" % author)
            for author in authors
        ))
        author = (pr.repository.project_id.github_name, pr.repository.project_id.github_email)

    committers = {
        (c['commit']['committer']['name'], c['commit']['committer']['email'])
        for c in commits
    }
    # should committers also be added to co-authors?
    committer = committers.pop() if len(committers) == 1 else None

    r = info.repo.merge_tree(info.head, pr.head)
    if r.returncode:
        raise exceptions.MergeError(pr, r.stderr)
    merge_tree = r.stdout.strip()

    r = info.repo.commit_tree(
        tree=merge_tree,
        parents=[info.head],
        message=str(msg),
        author=author,
        committer=committer or author,
    )
    if r.returncode:
        raise exceptions.MergeError(pr, r.stderr)
    head = r.stdout.strip()

    commits_map = {c['sha']: head for c in commits}
    commits_map[''] = head
    pr.commits_map = json.dumps(commits_map)

    return head

def stage_rebase_ff(pr: PullRequests, info: StagingSlice, commits: List[github.PrCommit], related_prs: PullRequests) -> str:
    add_self_references(pr, commits, related_prs=related_prs, merge=commits[-1])

    _logger.debug("rebasing %s on %s (commits=%s)",
                  pr.display_name, info.head, len(commits))
    head, mapping = info.repo.rebase(info.head, commits=commits)
    pr.commits_map = json.dumps({**mapping, '': head})
    return head

def stage_rebase_merge(pr: PullRequests, info: StagingSlice, commits: List[github.PrCommit], related_prs: PullRequests) -> str :
    add_self_references(pr, commits, related_prs=related_prs)
    _logger.debug("rebasing %s on %s (commits=%s)",
                  pr.display_name, info.head, len(commits))
    h, mapping = info.repo.rebase(info.head, commits=commits)
    msg = pr._build_message(pr, related_prs=related_prs)

    project = pr.repository.project_id
    merge_head= info.repo.merge(
        info.head, h, str(msg),
        author=(project.github_name, project.github_email),
    )
    pr.commits_map = json.dumps({**mapping, '': merge_head})
    return merge_head

def stage_merge(pr: PullRequests, info: StagingSlice, commits: List[github.PrCommit], related_prs: PullRequests) -> str:
    pr_head = commits[-1] # oldest to newest
    base_commit = None
    head_parents = {p['sha'] for p in pr_head['parents']}
    if len(head_parents) > 1:
        # look for parent(s?) of pr_head not in PR, means it's
        # from target (so we merged target in pr)
        merge = head_parents - {c['sha'] for c in commits}
        external_parents = len(merge)
        if external_parents > 1:
            raise exceptions.Unmergeable(
                "The PR head can only have one parent from the base branch "
                "(not part of the PR itself), found %d: %s" % (
                    external_parents,
                    ', '.join(merge)
                ))
        if external_parents == 1:
            [base_commit] = merge

    commits_map = {c['sha']: c['sha'] for c in commits}
    if base_commit:
        # replicate pr_head with base_commit replaced by
        # the current head
        t = info.repo.merge_tree(info.head, pr_head['sha'])
        if t.returncode:
            raise exceptions.MergeError(pr, t.stderr)
        merge_tree = t.stdout.strip()
        new_parents = [info.head] + list(head_parents - {base_commit})
        msg = pr._build_message(pr_head['commit']['message'], related_prs=related_prs)

        d2t = itemgetter('name', 'email', 'date')
        c = info.repo.commit_tree(
            tree=merge_tree,
            parents=new_parents,
            message=str(msg),
            author=d2t(pr_head['commit']['author']),
            committer=d2t(pr_head['commit']['committer']),
        )
        if c.returncode:
            raise exceptions.MergeError(pr, c.stderr)
        copy = c.stdout.strip()

        # merge commit *and old PR head* map to the pr head replica
        commits_map[''] = commits_map[pr_head['sha']] = copy
        pr.commits_map = json.dumps(commits_map)
        return copy
    else:
        # otherwise do a regular merge
        msg = pr._build_message(pr)
        project = pr.repository.project_id
        merge_head = info.repo.merge(
            info.head, pr.head, str(msg),
            author=(project.github_name, project.github_email),
        )
        # and the merge commit is the normal merge head
        commits_map[''] = merge_head
        pr.commits_map = json.dumps(commits_map)
        return merge_head

def is_mentioned(message: Union[PullRequests, str], pr: PullRequests, *, full_reference: bool = False) -> bool:
    """Returns whether ``pr`` is mentioned in ``message```
    """
    if full_reference:
        pattern = fr'\b{re.escape(pr.display_name)}\b'
    else:
        repository = pr.repository.name  # .replace('/', '\\/')
        pattern = fr'( |\b{repository})#{pr.number}\b'
    return bool(re.search(pattern, message if isinstance(message, str) else message.message))

def add_self_references(
        pr: PullRequests,
        commits: List[github.PrCommit],
        related_prs: PullRequests,
        merge: Optional[github.PrCommit] = None,
):
    """Adds a footer reference to ``self`` to all ``commits`` if they don't
    already refer to the PR.
    """
    for c in (c['commit'] for c in commits):
        c['message'] = str(pr._build_message(
            c['message'],
            related_prs=related_prs,
            merge=merge and c['url'] == merge['commit']['url'],
        ))

BREAK = re.compile(r'''
    [ ]{0,3} # 0-3 spaces of indentation
    # followed by a sequence of three or more matching -, _, or * characters,
    # each followed optionally by any number of spaces or tabs
    # so needs to start with a _, - or *, then have at least 2 more such
    # interspersed with any number of spaces or tabs
    ([*_-])
    ([ \t]*\1){2,}
    [ \t]*
''', flags=re.VERBOSE)
SETEX_UNDERLINE = re.compile(r'''
    [ ]{0,3} # no more than 3 spaces indentation
    [-=]+ # a sequence of = characters or a sequence of - characters
    [ ]* # any number of trailing spaces
    # we don't care about "a line containing a single -" because we want to
    # disambiguate SETEX headings from thematic breaks, and thematic breaks have
    # 3+ -. Doesn't look like GH interprets `- - -` as a line so yay...
''', flags=re.VERBOSE)
HEADER = re.compile('([A-Za-z-]+): (.*)')
class Message:
    @classmethod
    def from_message(cls, msg: Union[PullRequests, str]) -> 'Message':
        in_headers = True
        maybe_setex = None
        # creating from PR message -> remove content following break
        if isinstance(msg, str):
            message, handle_break = (msg, False)
        else:
            message, handle_break = (msg.message, True)
        headers = []
        body: List[str] = []
        # don't process the title (first line) of the commit message
        lines = message.splitlines()
        for line in reversed(lines[1:]):
            if maybe_setex:
                # NOTE: actually slightly more complicated: it's a SETEX heading
                #       only if preceding line(s) can be interpreted as a
                #       paragraph so e.g. a title followed by a line of dashes
                #       would indeed be a break, but this should be good enough
                #       for now, if we need more we'll need a full-blown
                #       markdown parser probably
                if line: # actually a SETEX title -> add underline to body then process current
                    body.append(maybe_setex)
                else: # actually break, remove body then process current
                    body = []
                maybe_setex = None

            if not line:
                if not in_headers and body and body[-1]:
                    body.append(line)
                continue

            if handle_break and BREAK.fullmatch(line):
                if SETEX_UNDERLINE.fullmatch(line):
                    maybe_setex = line
                else:
                    body = []
                continue

            h = HEADER.fullmatch(line)
            if h:
                # c-a-b = special case from an existing test, not sure if actually useful?
                if in_headers or h[1].lower() == 'co-authored-by':
                    headers.append(h.groups())
                    continue

            body.append(line)
            in_headers = False

        # if there are non-title body lines, add a separation after the title
        if body and body[-1]:
            body.append('')
        body.append(lines[0])
        return cls('\n'.join(reversed(body)), Headers(reversed(headers)))

    def __init__(self, body: str, headers: Optional[Headers] = None):
        self.body = body
        self.headers = headers or Headers()

    def __setattr__(self, name, value):
        # make sure stored body is always stripped
        if name == 'body':
            value = value and value.strip()
        super().__setattr__(name, value)

    def __str__(self):
        if not self.headers:
            return self.body.rstrip() + '\n'

        with io.StringIO() as msg:
            msg.write(self.body.rstrip())
            msg.write('\n\n')
            # https://git.wiki.kernel.org/index.php/CommitMessageConventions
            # seems to mostly use capitalised names (rather than title-cased)
            keys = list(OrderedSet(k.capitalize() for k in self.headers.keys()))
            # c-a-b must be at the very end otherwise github doesn't see it
            keys.sort(key=lambda k: k == 'Co-authored-by')
            for k in keys:
                for v in self.headers.getlist(k):
                    msg.write(k)
                    msg.write(': ')
                    msg.write(v)
                    msg.write('\n')

            return msg.getvalue()
