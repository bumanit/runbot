import xmlrpc.client

import pytest

from utils import Commit

# basic udiff / show style patch, updates `b` from `1` to `2`
BASIC_UDIFF = """\
commit 0000000000000000000000000000000000000000
Author: 3 Discos Down <bar@example.org>
Date:   2021-04-24T17:09:14Z
 
    whop
    
    whop whop
 
diff --git a/b b/b
index 000000000000..000000000000 100644
--- a/b
+++ b/b
@@ -1,1 +1,1 @@
-1
+2
"""

FIELD_MAPPING = {
    'boolean': ('old_value_integer', 'new_value_integer'),
    'date': ('old_value_datetime', 'new_value_datetime'),
    'datetime': ('old_value_datetime', 'new_value_datetime'),
    'char': ('old_value_char', 'new_value_char'),
    'float': ('old_value_float', 'new_value_float'),
    'integer': ('old_value_integer', 'new_value_integer'),
    'monetary': ('old_value_float', 'new_value_float'),
    'text': ('old_value_text', 'new_value_text'),
}
def read_tracking_value(tv):
    field_id = tv.field_id if 'field_id' in tv else tv.field
    field_type = field_id.field_type if 'field_type' in field_id else field_id.ttype
    old_field_name, new_field_name = FIELD_MAPPING[field_type]
    return field_id.name, tv[old_field_name], tv[new_field_name]


@pytest.fixture(autouse=True)
def _setup(repo):
    with repo:
        [c, _] = repo.make_commits(
            None,
            Commit("a", tree={"a": "1", "b": "1\n"}),
            Commit("b", tree={"a": "2"}),
            ref="heads/master",
        )
        repo.make_ref("heads/x", c)

@pytest.mark.parametrize("group,access", [
    ('base.group_portal', False),
    ('base.group_user', False),
    ('runbot_merge.group_patcher', True),
    ('runbot_merge.group_admin', False),
    ('base.group_system', True),
])
def test_patch_acl(env, project, group, access):
    g = env.ref(group)
    assert g._name == 'res.groups'
    env['res.users'].create({
        'name': 'xxx',
        'login': 'xxx',
        'password': 'xxx',
        'groups_id': [(6, 0, [g.id])],
    })
    env2 = env.with_user('xxx', 'xxx')
    def create():
        return env2['runbot_merge.patch'].create({
            'target': project.branch_ids.id,
            'repository': project.repo_ids.id,
            'patch': BASIC_UDIFF,
        })
    if access:
        create()
    else:
        pytest.raises(xmlrpc.client.Fault, create)\
            .match("You are not allowed to create")

def test_apply_commit(env, project, repo, users):
    with repo:
        [c] = repo.make_commits("x", Commit("c", tree={"b": "2"}, author={
            'name': "Henry Hoover",
            "email": "dustsuckinghose@example.org",
        }), ref="heads/abranch")
        repo.delete_ref('heads/abranch')

    p = env['runbot_merge.patch'].create({
        'target': project.branch_ids.id,
        'repository': project.repo_ids.id,
        'commit': c,
    })

    env.run_crons()

    HEAD = repo.commit('master')
    assert repo.read_tree(HEAD) == {
        'a': '2',
        'b': '2',
    }
    assert HEAD.message == "c"
    assert HEAD.author['name'] == "Henry Hoover"
    assert HEAD.author['email'] == "dustsuckinghose@example.org"
    assert not p.active

def test_commit_conflict(env, project, repo, users):
    with repo:
        [c] = repo.make_commits("x", Commit("x", tree={"b": "3"}))
        repo.make_commits("master", Commit("c", tree={"b": "2"}), ref="heads/master", make=False)

    p = env['runbot_merge.patch'].create({
        'target': project.branch_ids.id,
        'repository': project.repo_ids.id,
        'commit': c,
    })

    env.run_crons()

    HEAD = repo.commit('master')
    assert repo.read_tree(HEAD) == {
        'a': '2',
        'b': '2',
    }
    assert not p.active
    assert [(
        m.subject,
        m.body,
        list(map(read_tracking_value, m.tracking_value_ids)),
    )
        for m in reversed(p.message_ids)
    ] == [
        (False, '<p>Unstaged direct-application patch created</p>', []),
        (
            "Unable to apply patch",
            """\
<p>Auto-merging b<br>\
CONFLICT (content): Merge conflict in b<br></p>\
""",
            [],
        ),
        (False, '', [('active', 1, 0)]),
    ]

def test_apply_udiff(env, project, repo, users):
    p = env['runbot_merge.patch'].create({
        'target': project.branch_ids.id,
        'repository': project.repo_ids.id,
        'patch': BASIC_UDIFF,
    })

    env.run_crons()

    HEAD = repo.commit('master')
    assert repo.read_tree(HEAD) == {
        'a': '2',
        'b': '2\n',
    }
    assert HEAD.message == "whop\n\nwhop whop"
    assert HEAD.author['name'] == "3 Discos Down"
    assert HEAD.author['email'] == "bar@example.org"
    assert not p.active


def test_apply_format_patch(env, project, repo, users):
    p = env['runbot_merge.patch'].create({
        'target': project.branch_ids.id,
        'repository': project.repo_ids.id,
        'patch': """\
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: 3 Discos Down <bar@example.org>
Date: Sat, 24 Apr 2021 17:09:14 +0000
Subject: [PATCH] whop

whop whop
---
 b | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
 
diff --git a/b b/b
index 000000000000..000000000000 100644
--- a/b
+++ b/b
@@ -1,1 +1,1 @@
-1
+2
-- 
2.46.2
""",
    })

    env.run_crons()

    HEAD = repo.commit('master')
    assert repo.read_tree(HEAD) == {
        'a': '2',
        'b': '2\n',
    }
    assert HEAD.message == "whop\n\nwhop whop"
    assert HEAD.author['name'] == "3 Discos Down"
    assert HEAD.author['email'] == "bar@example.org"
    assert not p.active

def test_patch_conflict(env, project, repo, users):
    p = env['runbot_merge.patch'].create({
        'target': project.branch_ids.id,
        'repository': project.repo_ids.id,
        'patch': BASIC_UDIFF,
    })
    with repo:
        repo.make_commits('master', Commit('cccombo breaker', tree={'b': '3'}), ref='heads/master', make=False)

    env.run_crons()

    HEAD = repo.commit('master')
    assert HEAD.message == 'cccombo breaker'
    assert repo.read_tree(HEAD) == {
        'a': '2',
        'b': '3',
    }
    assert not p.active
    assert [(
        m.subject,
        m.body,
        list(map(read_tracking_value, m.tracking_value_ids)),
    )
        for m in reversed(p.message_ids)
    ] == [(
        False,
        '<p>Unstaged direct-application patch created</p>',
        [],
    ), (
        "Unable to apply patch",
        "<p>patching file b<br>Hunk #1 FAILED at 1.<br>1 out of 1 hunk FAILED -- saving rejects to file b.rej<br></p>",
        [],
    ), (
        False, '', [('active', 1, 0)]
    )]
