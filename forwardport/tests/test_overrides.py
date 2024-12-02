import json

from utils import Commit, make_basic, to_pr


def statuses(pr):
    return {
        k: v['state']
        for k, v in json.loads(pr.statuses_full).items()
    }
def test_override_inherited(env, config, make_repo, users):
    """ A forwardport should inherit its parents' overrides, until it's edited.
    """
    repo, other = make_basic(env, config, make_repo)
    project = env['runbot_merge.project'].search([])
    project.repo_ids.status_ids = [(5, 0, 0), (0, 0, {'context': 'default'})]
    env['res.partner'].search([('github_login', '=', users['reviewer'])])\
        .write({'override_rights': [(0, 0, {
            'repository_id': project.repo_ids.id,
            'context': 'default',
        })]})

    with repo:
        repo.make_commits('a', Commit('pr 1', tree={'a': '0'}), ref='heads/change')
        pr = repo.make_pr(target='a', head='change')
        pr.post_comment('hansen r+ override=default', config['role_reviewer']['token'])
    env.run_crons()

    original = to_pr(env, pr)
    assert original.state == 'ready'
    assert not original.limit_id

    with repo:
        repo.post_status('staging.a', 'success')
    env.run_crons()

    pr0_id, pr1_id, pr2_id = env['runbot_merge.pull_requests'].search([], order='number')
    assert pr0_id == original
    assert pr0_id.target.name == 'a'

    assert pr1_id.parent_id == pr0_id
    assert pr1_id.number == 2
    assert pr1_id.target.name == 'b'
    assert pr1_id.state == 'validated'
    assert statuses(pr1_id) == {'default': 'success'}

    assert pr2_id.parent_id == pr1_id
    assert pr2_id.target.name == 'c'
    assert pr2_id.state == 'validated'
    assert statuses(pr2_id) == {'default': 'success'}

    # now we edit the child PR
    pr1 = repo.get_pr(pr1_id.number)
    pr_repo, pr_ref = pr1.branch
    with pr_repo:
        pr_repo.make_commits(
            pr1_id.target.name,
            Commit('wop wop', tree={'a': '1'}),
            ref=f'heads/{pr_ref}',
            make=False
        )
    env.run_crons()
    assert pr1_id.state == 'opened'
    assert not pr1_id.parent_id
    assert statuses(pr1_id) == {}, "should not have any status left"
    assert statuses(pr2_id) == {}

    with repo:
        pr1.post_comment('hansen override=default', config['role_reviewer']['token'])
    assert statuses(pr1_id) == {'default': 'success'}
    assert statuses(pr2_id) == {'default': 'success'}

def test_override_combination(env, config, make_repo, users):
    """ A forwardport should inherit its parents' overrides, until it's edited.
    """
    repo, other = make_basic(env, config, make_repo)
    project = env['runbot_merge.project'].search([])
    env['res.partner'].search([('github_login', '=', users['reviewer'])]) \
        .write({'override_rights': [
        (0, 0, {
            'repository_id': project.repo_ids.id,
            'context': 'ci/runbot',
        }),
        (0, 0, {
            'repository_id': project.repo_ids.id,
            'context': 'legal/cla',
        })
    ]})

    with repo:
        repo.make_commits('a', Commit('C', tree={'a': '0'}), ref='heads/change')
        pr = repo.make_pr(target='a', head='change')
        repo.post_status('change', 'success', 'legal/cla')
        pr.post_comment('hansen r+ override=ci/runbot', config['role_reviewer']['token'])
    env.run_crons()

    pr0_id = to_pr(env, pr)
    assert pr0_id.state == 'ready'
    assert statuses(pr0_id) == {'ci/runbot': 'success', 'legal/cla': 'success'}

    with repo:
        repo.post_status('staging.a', 'success', 'legal/cla')
        repo.post_status('staging.a', 'success', 'ci/runbot')
    env.run_crons()

    # check for combination: ci/runbot is overridden through parent, if we
    # override legal/cla then the PR should be validated
    pr1_id =  env['runbot_merge.pull_requests'].search([('parent_id', '=', pr0_id.id)])
    assert pr1_id.state == 'opened'
    assert statuses(pr1_id) == {'ci/runbot': 'success'}
    with repo:
        repo.get_pr(pr1_id.number).post_comment('hansen override=legal/cla', config['role_reviewer']['token'])
    env.run_crons()
    assert pr1_id.state == 'validated'

    # editing the child should devalidate
    pr_repo, pr_ref = repo.get_pr(pr1_id.number).branch
    with pr_repo:
        pr_repo.make_commits(
            pr1_id.target.name,
            Commit('wop wop', tree={'a': '1'}),
            ref=f'heads/{pr_ref}',
            make=False
        )
    env.run_crons()
    assert pr1_id.state == 'opened'
    assert not pr1_id.parent_id
    assert statuses(pr1_id) == {'legal/cla': 'success'}, \
        "should only have its own status left"
