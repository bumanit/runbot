# -*- coding: utf-8 -*-
import contextlib
import itertools
import re
import time
import typing

from lxml import html

MESSAGE_TEMPLATE = """{message}

closes {repo}#{number}

{headers}Signed-off-by: {name} <{email}>"""
# target branch '-' source branch '-' base64 unique '-fw'
REF_PATTERN = r'{target}-{source}-[a-zA-Z0-9_-]{{4}}-fw'

class Commit:
    def __init__(self, message, *, author=None, committer=None, tree, reset=False):
        self.id = None
        self.message = message
        self.author = author
        self.committer = committer
        self.tree = tree
        self.reset = reset

def validate_all(repos, refs, contexts=('ci/runbot', 'legal/cla')):
    """ Post a "success" status for each context on each ref of each repo
    """
    for repo, branch, context in itertools.product(repos, refs, contexts):
        repo.post_status(branch, 'success', context)

def get_partner(env, gh_login):
    return env['res.partner'].search([('github_login', '=', gh_login)])

def _simple_init(repo):
    """ Creates a very simple initialisation: a master branch with a commit,
    and a PR by 'user' with two commits, targeted to the master branch
    """
    m = repo.make_commit(None, 'initial', None, tree={'m': 'm'})
    repo.make_ref('heads/master', m)
    c1 = repo.make_commit(m, 'first', None, tree={'m': 'c1'})
    c2 = repo.make_commit(c1, 'second', None, tree={'m': 'c2'})
    prx = repo.make_pr(title='title', body='body', target='master', head=c2)
    return prx

class matches(str):
    # necessary so str.__new__ does not freak out on `flags`
    def __new__(cls, pattern, flags=0):
        return super().__new__(cls, pattern)

    def __init__(self, pattern, flags=0):
        p, n = re.subn(
            # `re.escape` will escape the `$`, so we need to handle that...
            # maybe it should not be $?
            r'\\\$(\w*?)\\\$',
            lambda m: f'(?P<{m[1]}>.*?)' if m[1] else '(.*?)',
            re.escape(self),
        )
        assert n, f"matches' pattern should have at least one placeholder, found none in\n{pattern}"
        self._r = re.compile(p, flags | re.DOTALL)

    def __eq__(self, text):
        if not isinstance(text, str):
            return NotImplemented
        return self._r.search(text)

def seen(env, pr, users):
    url = to_pr(env, pr).url
    return users['user'], f'[![Pull request status dashboard]({url}.png)]({url})'

def make_basic(
        env,
        config,
        make_repo,
        *,
        project_name='myproject',
        reponame='proj',
        statuses='legal/cla,ci/runbot',
        fp_token=True,
        fp_remote=True,
):
    """ Creates a project ``project_name`` **if none exists**, otherwise
    retrieves the existing one and adds a new repository and its fork.

    Repositories are setup with three forking branches:

    ::

        f = 0 -- 1 -- 2 -- 3 -- 4  : a
                      |
        g =           `-- 11 -- 22 : b
                         |
        h =               `-- 111  : c

    each branch just adds and modifies a file (resp. f, g and h) through the
    contents sequence a b c d e

    :param env: Environment, for odoo model interactions
    :param config: pytest project config thingie
    :param make_repo: repo maker function, normally the fixture, should be a
                      ``Callable[[str], Repo]``
    :param project_name: internal project name, can be used to recover the
                         project object afterward, matches exactly since it's
                         unique per odoo db (and thus test)
    :param reponame: the base name of the repository, for identification, for
                     concurrency reasons the actual repository name *will* be
                     different
    :param statuses: required statuses for the repository, stupidly default to
                     the old Odoo statuses, should be moved to ``default`` over
                     time for simplicity (unless the test specifically calls for
                     multiple statuses)
    :param fp_token: whether to set the ``fp_github_token`` on the project if
                     / when creating it
    :param fp_remote: whether to create a fork repo and set it as the
                      repository's ``fp_remote_target``
    """
    Projects = env['runbot_merge.project']
    project = Projects.search([('name', '=', project_name)])
    if not project:
        project = env['runbot_merge.project'].create({
            'name': project_name,
            'github_token': config['github']['token'],
            'github_prefix': 'hansen',
            'github_name': config['github']['name'],
            'github_email': "foo@example.org",
            'fp_github_token': fp_token and config['github']['token'],
            'fp_github_name': 'herbert',
            'branch_ids': [
                (0, 0, {'name': 'a', 'sequence': 100}),
                (0, 0, {'name': 'b', 'sequence': 80}),
                (0, 0, {'name': 'c', 'sequence': 60}),
            ],
        })

    prod = make_repo(reponame)
    env['runbot_merge.events_sources'].create({'repository': prod.name})
    with prod:
        _0, _1, a_2, _3, _4, = prod.make_commits(
            None,
            Commit("0", tree={'f': 'a'}),
            Commit("1", tree={'f': 'b'}),
            Commit("2", tree={'f': 'c'}),
            Commit("3", tree={'f': 'd'}),
            Commit("4", tree={'f': 'e'}),
            ref='heads/a',
        )
        b_1, _2 = prod.make_commits(
            a_2,
            Commit('11', tree={'g': 'a'}),
            Commit('22', tree={'g': 'b'}),
            ref='heads/b',
        )
        prod.make_commits(
            b_1,
            Commit('111', tree={'h': 'a'}),
            ref='heads/c',
        )
    other = prod.fork() if fp_remote else None
    repo = env['runbot_merge.repository'].create({
        'project_id': project.id,
        'name': prod.name,
        'required_statuses': statuses,
        'fp_remote_target': other.name if other else False,
        'group_id': False,
    })
    env['res.partner'].search([
        ('github_login', '=', config['role_reviewer']['user'])
    ]).write({
        'review_rights': [(0, 0, {'repository_id': repo.id, 'review': True})]
    })
    env['res.partner'].search([
        ('github_login', '=', config['role_self_reviewer']['user'])
    ]).write({
        'review_rights': [(0, 0, {'repository_id': repo.id, 'self_review': True})]
    })

    return prod, other

def pr_page(page, pr):
    return html.fromstring(page(f'/{pr.repo.name}/pull/{pr.number}'))

def to_pr(env, pr):
    for _ in range(5):
        pr_id = env['runbot_merge.pull_requests'].search([
            ('repository.name', '=', pr.repo.name),
            ('number', '=', pr.number),
        ])
        if pr_id:
            assert len(pr_id) == 1, f"Expected to find {pr.repo.name}#{pr.number}, got {pr_id}."
            return pr_id
        time.sleep(1)

    raise TimeoutError(f"Unable to find {pr.repo.name}#{pr.number}")

def part_of(label, pr_id, *, separator='\n\n'):
    """ Adds the "part-of" pseudo-header in the footer.
    """
    return f"""\
{label}{separator}\
Part-of: {pr_id.display_name}
Signed-off-by: {pr_id.reviewed_by.formatted_email}"""

def ensure_one(records):
    assert len(records) == 1
    return records


@contextlib.contextmanager
def prevent_unstaging(st) -> None:
    # hack: `Stagings.cancel` only cancels *active* stagings,
    # so if we disable the staging, do a thing, then re-enable
    # then things should work out
    assert st and st.active, "preventing unstaging of not-a-staging is not useful"
    st.active = False
    try:
        yield
    finally:
        st.active = True


TYPE_MAPPING = {
    'boolean': 'integer',
    'date': 'datetime',
    'monetary': 'float',
    'selection': 'char',
    'many2one': 'char',
}
def read_tracking_value(tv) -> tuple[str, typing.Any, typing.Any]:
    field_id = tv.field_id if 'field_id' in tv else tv.field
    field_type = field_id.field_type if 'field_type' in field_id else field_id.ttype
    t = TYPE_MAPPING.get(field_type) or field_type
    return field_id.name, tv[f"old_value_{t}"], tv[f"new_value_{t}"]
