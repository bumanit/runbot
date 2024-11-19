from utils import Commit, to_pr, make_basic, prevent_unstaging


def test_staging_disabled_branch(env, project, repo, config):
    """Check that it's possible to disable staging on a specific branch
    """
    project.branch_ids = [(0, 0, {
        'name': 'other',
        'staging_enabled': False,
    })]
    with repo:
        [master_commit] = repo.make_commits(None, Commit("master", tree={'a': '1'}), ref="heads/master")
        [c1] = repo.make_commits(master_commit, Commit("thing", tree={'a': '2'}), ref='heads/master-thing')
        master_pr = repo.make_pr(title="whatever", target="master", head="master-thing")
        master_pr.post_comment("hansen r+", config['role_reviewer']['token'])
        repo.post_status(c1, 'success')

        [other_commit] = repo.make_commits(None, Commit("other", tree={'b': '1'}), ref='heads/other')
        [c2] = repo.make_commits(other_commit, Commit("thing", tree={'b': '2'}), ref='heads/other-thing')
        other_pr = repo.make_pr(title="whatever", target="other", head="other-thing")
        other_pr.post_comment("hansen r+", config['role_reviewer']['token'])
        repo.post_status(c2, 'success')
    env.run_crons()

    assert to_pr(env, master_pr).staging_id, \
        "master is allowed to stage, should be staged"
    assert not to_pr(env, other_pr).staging_id, \
        "other is *not* allowed to stage, should not be staged"


def test_staged_failure(env, config, repo, users):
    """If a PR is staged and gets a new CI failure, it should be unstaged

    This was an issue with odoo/odoo#165931 which got rebuilt and that triggered
    a failure, which made the PR !ready but kept the staging going. So the PR
    ended up in an odd state of being both staged and not ready.

    And while the CI failure it got was a false positive, it was in fact the
    problematic PR breaking that staging.

    More relevant the runbot's "automatic rebase" mode sends CI to the original
    commits so receiving legitimate failures after staging very much makes
    sense e.g. an old PR is staged, the staging starts failing, somebody notices
    the outdated PR and triggers autorebase, which fails (because something
    incompatible was merged in the meantime), the PR *should* be unstaged.
    """
    with repo:
        repo.make_commits(None, Commit("master", tree={'a': '1'}), ref="heads/master")

        repo.make_commits('master', Commit('c', tree={'a': 'b'}), ref="heads/mybranch")
        pr = repo.make_pr(target='master', head='mybranch')
        repo.post_status('mybranch', 'success')
        pr.post_comment('hansen r+', config['role_reviewer']['token'])
    env.run_crons()

    pr_id = to_pr(env, pr)
    staging = pr_id.staging_id
    assert staging, "pr should be staged"

    with repo:
        # started rebuild, nothing should happen
        repo.post_status('mybranch', 'pending')
    env.run_crons()
    assert pr_id.staging_id
    # can't find a clean way to keep this "ready" when transitioning from
    # success to pending *without updating the head*, at least not without
    # adding a lot of contextual information around `_compute_statuses`
    # assert pr_id.state == 'ready'

    with repo:
        # rebuild failed omg!
        repo.post_status('mybranch', 'failure')
    env.run_crons()

    assert pr_id.status == 'failure'
    assert pr_id.state == 'approved'

    assert not pr_id.staging_id, "pr should be unstaged"
    assert staging.state == "cancelled"
    assert staging.reason == f"{pr_id.display_name} had CI failure after staging"


def test_update_unready(env, config, repo, users):
    """Less likely with `test_staged_failure` fixing most of the issue, but
    clearly the assumption that a staged PR will be `ready` is not strictly
    enforced.

    As such, updating the PR should try to `unstage` it no matter what state
    it's in, this will lead to slightly higher loads on sync but loads on the
    mergebot are generally non-existent outside of the git maintenance cron,
    and there are doubtless other optimisations missing, or that (and other
    items) can be done asynchronously.
    """
    with repo:
        repo.make_commits(None, Commit("master", tree={'a': '1'}), ref="heads/master")

        repo.make_commits('master', Commit('c', tree={'a': 'b'}), ref="heads/mybranch")
        pr = repo.make_pr(target='master', head='mybranch')
        repo.post_status('mybranch', 'success')
        pr.post_comment('hansen r+', config['role_reviewer']['token'])

        [c2] = repo.make_commits('master', Commit('c', tree={'a': 'c'}))
    env.run_crons()

    pr_id = to_pr(env, pr)
    staging = pr_id.staging_id
    assert staging, "pr should be staged"

    with prevent_unstaging(pr_id.staging_id):
        pr_id.overrides = '{"default": {"state": "failure"}}'
    assert pr_id.state == "approved"
    assert pr_id.staging_id, "pr should not have been unstaged because we cheated"

    with repo:
        repo.update_ref("heads/mybranch", c2, force=True)
    env.run_crons()

    assert not pr_id.staging_id, "pr should be unstaged"
    assert staging.state == "cancelled"
    assert staging.reason == f"{pr_id.display_name} updated by {users['user']}"
