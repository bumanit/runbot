"""This is a bunch of tests for the automated flagging of stagings as likely
failed due to false positives / non-deterministic failures.
"""
import pytest

from utils import Commit, to_pr

def setup_prs(env, repo, config):
    with repo:
        m = repo.make_commits(
            None,
            Commit("root", tree={'a': ''}),
            ref="heads/master",
        )
        repo.make_commits(m, Commit("c1", tree={'1': ''}), ref="heads/feature1")
        pr1 = repo.make_pr(title="whatever", target="master", head="feature1")
        repo.make_commits(m, Commit("c2", tree={'2': ''}), ref="heads/feature2")
        pr2 = repo.make_pr(title="whatever", target="master", head="feature2")
    env.run_crons(None)

    with repo:
        pr1.post_comment("hansen r+", config['role_reviewer']['token'])
        repo.post_status(pr1.head, 'success')
        pr2.post_comment("hansen r+", config['role_reviewer']['token'])
        repo.post_status(pr2.head, 'success')
    env.run_crons(None)

    return pr1, pr2

def test_false_positive(env, repo, users, config):
    """ If we end up merging everything, consider that the original error was a
    false positive
    """
    pr1, pr2 = setup_prs(env, repo, config)

    staging = env['runbot_merge.stagings'].search([])
    assert staging
    assert staging.likely_false_positive == False

    with repo:
        repo.post_status('staging.master', 'failure')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    # all splits succeeded => original failure was likely a false positive or non-deterministic
    with repo:
        repo.post_status('staging.master', 'success')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    with repo:
        repo.post_status('staging.master', 'success')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    assert to_pr(env, pr1).state == 'merged'
    assert to_pr(env, pr2).state == 'merged'

def test_success_is_not_flagged(env, repo, users, config):
    setup_prs(env, repo, config)

    staging = env['runbot_merge.stagings'].search([])
    assert staging
    assert staging.likely_false_positive == False

    with repo:
        repo.post_status('staging.master', 'success')
    env.run_crons(None)
    assert staging.likely_false_positive == False

def test_true_failure_is_not_flagged(env, repo, users, config):
    """ If we end up flagging (at least) one specific PR, assume it's a true
    positive, even though enough false positives can end like that too.
    """
    pr1, pr2 = setup_prs(env, repo, config)

    staging = env['runbot_merge.stagings'].search([])
    assert staging
    assert staging.likely_false_positive == False

    with repo:
        repo.post_status('staging.master', 'failure')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    with repo:
        repo.post_status('staging.master', 'success')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    # PR pinpointed as a true error => no false positive
    with repo:
        repo.post_status('staging.master', 'failure')
    env.run_crons(None)
    assert staging.likely_false_positive == False

    assert to_pr(env, pr1).state == 'merged'
    assert to_pr(env, pr2).state == 'error'

def test_cancel_staging_not_flagged(env, repo, users, config):
    """ If we cancel a staging, assume there was a legit reason to do so and if
    there's a false positive we already found it.
    """
    setup_prs(env, repo, config)

    staging = env['runbot_merge.stagings'].search([])
    assert staging
    assert staging.likely_false_positive == False

    with repo:
        repo.post_status('staging.master', 'failure')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    # `staging` is the source and not active anymore, can't cancel it
    staging.target.active_staging_id.cancel("because")
    assert staging.likely_false_positive == False

def test_removed_split_not_flagged(env, repo, users, config):
    """ If we delete a split, basically the same idea as for cancelling stagings
    """
    setup_prs(env, repo, config)

    staging = env['runbot_merge.stagings'].search([])
    assert staging
    assert staging.likely_false_positive == False

    with repo:
        repo.post_status('staging.master', 'failure')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    staging.target.split_ids.unlink()
    assert staging.likely_false_positive == False

@pytest.mark.parametrize('pr_index', [0, 1])
def test_unstaged_pr_not_flagged(env, repo, users, config, pr_index):
    """ If we cancel a staging by unstaging a PR or remove a PR from a split,
    assume it's because the PR caused a true failure
    """
    prs = setup_prs(env, repo, config)

    staging = env['runbot_merge.stagings'].search([])
    assert staging
    assert staging.likely_false_positive == False

    with repo:
        repo.post_status('staging.master', 'failure')
    env.run_crons(None)
    assert staging.likely_false_positive == True

    with repo:
        prs[pr_index].post_comment("hansen r-", config['role_reviewer']['token'])

    assert staging.likely_false_positive == False
