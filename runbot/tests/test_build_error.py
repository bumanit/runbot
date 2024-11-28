import hashlib

from odoo import fields
from odoo.exceptions import ValidationError
from .common import RunbotCase

RTE_ERROR = """FAIL: TestUiTranslate.test_admin_tour_rte_translator
Traceback (most recent call last):
  File "/data/build/odoo/addons/website/tests/test_ui.py", line 89, in test_admin_tour_rte_translator
    self.start_tour("/", 'rte_translator', login='admin', timeout=120)
  File "/data/build/odoo/odoo/tests/common.py", line 1062, in start_tour
    res = self.browser_js(url_path=url_path, code=code, ready=ready, **kwargs)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/data/build/odoo/odoo/tests/common.py", line 1046, in browser_js
    self.fail('%s\n%s' % (message, error))
AssertionError: The test code "odoo.startTour('rte_translator')" failed
Tour rte_translator failed at step click language dropdown (trigger: .js_language_selector .dropdown-toggle)
"""


class TestBuildError(RunbotCase):

    def create_test_build(self, vals):
        create_vals = {
            'params_id': self.base_params.id,
            'port': '1234',
            'local_result': 'ok'
        }
        create_vals.update(vals)
        return self.Build.create(create_vals)

    def create_params(self, vals):
        create_vals = {
            'version_id': self.version_13.id,
            'project_id': self.project.id,
            'config_id': self.default_config.id,
            'create_batch_id': self.dev_batch.id,
        }
        create_vals.update(vals)
        return self.BuildParameters.create(create_vals)

    def create_log(self, vals):
        log_vals = {
            'level': 'ERROR',
            'type': 'server',
            'name': 'test-build-error-name',
            'path': '/data/build/server/addons/web_studio/tests/test_ui.py',
            'func': 'test-build-error-func',
            'line': 1,
        }
        log_vals.update(vals)
        return self.IrLog.create(log_vals)


    def setUp(self):
        super(TestBuildError, self).setUp()
        self.BuildError = self.env['runbot.build.error']
        self.BuildErrorContent = self.env['runbot.build.error.content']
        self.BuildErrorLink = self.env['runbot.build.error.link']
        self.RunbotTeam = self.env['runbot.team']
        self.ErrorRegex = self.env['runbot.error.regex']
        self.IrLog = self.env['ir.logging']

    def test_create_write_clean(self):

        self.ErrorRegex.create({
            'regex': r'\d+',
            're_type': 'cleaning',
        })

        error_content = self.BuildErrorContent.create({
            'content': 'foo bar 242',
        })

        expected = 'foo bar %'
        expected_hash = hashlib.sha256(expected.encode()).hexdigest()
        self.assertEqual(error_content.cleaned_content, expected)
        self.assertEqual(error_content.fingerprint, expected_hash)

        # Let's ensure that the fingerprint changes if we clean with an additional regex
        self.ErrorRegex.create({
            'regex': 'bar',
            're_type': 'cleaning',
        })
        error_content.action_clean_content()
        expected = 'foo % %'
        expected_hash = hashlib.sha256(expected.encode()).hexdigest()
        self.assertEqual(error_content.cleaned_content, expected)
        self.assertEqual(error_content.fingerprint, expected_hash)

    def test_fields(self):
        version_1 = self.Version.create({'name': '1.0'})
        version_2 = self.Version.create({'name': '2.0'})
        bundle_1 = self.Bundle.create({'name': 'v1', 'project_id': self.project.id})
        bundle_2 = self.Bundle.create({'name': 'v2', 'project_id': self.project.id})
        batch_1 = self.Batch.create({'bundle_id': bundle_1.id})
        batch_2 = self.Batch.create({'bundle_id': bundle_2.id})

        params_1 = self.BuildParameters.create({
            'version_id': version_1.id,
            'project_id': self.project.id,
            'config_id': self.default_config.id,
            'create_batch_id': batch_1.id,
        })
        params_2 = self.BuildParameters.create({
            'version_id': version_2.id,
            'project_id': self.project.id,
            'config_id': self.default_config.id,
            'create_batch_id': batch_2.id,
        })

        build_1 = self.Build.create({
            'local_result': 'ko',
            'local_state': 'done',
            'params_id': params_1.id,
        })
        build_2 = self.Build.create({
            'local_result': 'ko',
            'local_state': 'done',
            'params_id': params_2.id,
        })

        self.env['runbot.batch.slot'].create({
            'build_id': build_1.id,
            'batch_id': batch_1.id,
            'params_id': build_1.params_id.id,
            'link_type': 'created',
        })
        self.env['runbot.batch.slot'].create({
            'build_id': build_2.id,
            'batch_id': batch_2.id,
            'params_id': build_2.params_id.id,
            'link_type': 'created',
        })

        error = self.BuildError.create({})
        error_content_1 = self.BuildErrorContent.create({'content': 'foo bar v1', 'error_id': error.id})
        error_content_2 = self.BuildErrorContent.create({'content': 'foo bar v2', 'error_id': error.id})
        error_content_2b = self.BuildErrorContent.create({'content': 'bar v2', 'error_id': error.id})
        l_1 = self.BuildErrorLink.create({'build_id': build_1.id, 'error_content_id': error_content_1.id})
        l_2 = self.BuildErrorLink.create({'build_id': build_2.id, 'error_content_id': error_content_2.id})
        l_3 = self.BuildErrorLink.create({'build_id': build_2.id, 'error_content_id': error_content_2b.id})

        self.assertEqual(error_content_1.build_ids, build_1)
        self.assertEqual(error_content_2.build_ids, build_2)
        self.assertEqual(error_content_2b.build_ids, build_2)
        self.assertEqual(error.build_ids, build_1 | build_2)

        self.assertEqual(error_content_1.bundle_ids, bundle_1)
        self.assertEqual(error_content_2.bundle_ids, bundle_2)
        self.assertEqual(error_content_2b.bundle_ids, bundle_2)
        self.assertEqual(error.bundle_ids, bundle_1 | bundle_2)

        self.assertEqual(error_content_1.version_ids, version_1)
        self.assertEqual(error_content_2.version_ids, version_2)
        self.assertEqual(error_content_2b.version_ids, version_2)
        self.assertEqual(error.version_ids, version_1 | version_2)

        self.assertEqual(error_content_1.build_error_link_ids, l_1)
        self.assertEqual(error_content_2.build_error_link_ids, l_2)
        self.assertEqual(error_content_2b.build_error_link_ids, l_3)
        self.assertEqual(error.build_error_link_ids, l_1 | l_2 | l_3)
        self.assertEqual(error.unique_build_error_link_ids, l_1 | l_2)

    def test_merge_test_tags(self):
        error_a = self.BuildError.create({
            'content': 'foo',
        })
        error_b = self.BuildError.create({
            'content': 'bar',
            'test_tags': 'blah',
        })

        self.assertEqual(self.BuildError._disabling_tags(), ['-blah'])

        error_a._merge(error_b)

        self.assertEqual(self.BuildError._disabling_tags(), ['-blah'])
        self.assertEqual(error_a.test_tags, 'blah')
        self.assertEqual(error_b.test_tags, False)
        self.assertEqual(error_b.active, False)

    def test_relink_contents(self):
        build_a = self.create_test_build({'local_result': 'ko', 'local_state': 'done'})
        error_content_a = self.BuildErrorContent.create({'content': 'foo bar'})
        self.BuildErrorLink.create({'build_id': build_a.id, 'error_content_id': error_content_a.id})
        error_a = error_content_a.error_id

        build_b = self.create_test_build({'local_result': 'ko', 'local_state': 'done'})
        error_content_b = self.BuildErrorContent.create({'content': 'foo bar'})
        self.BuildErrorLink.create({'build_id': build_b.id, 'error_content_id': error_content_b.id})
        error_b = error_content_b.error_id
        self.assertNotEqual(error_a, error_b)
        self.assertEqual(self.BuildErrorContent.search([('fingerprint', '=', error_content_a.fingerprint)]), error_content_a | error_content_b)
        (error_content_a | error_content_b)._relink()
        self.assertEqual(self.BuildErrorContent.search([('fingerprint', '=', error_content_a.fingerprint)]), error_content_a)
        self.assertTrue(error_a.active, 'The first merged error should stay active')
        self.assertFalse(error_b.active, 'The second merged error should have stay deactivated')
        self.assertIn(build_a, error_a.build_error_link_ids.build_id)
        self.assertIn(build_a, error_a.build_ids)
        self.assertIn(build_b, error_a.build_error_link_ids.build_id)
        self.assertIn(build_b, error_a.build_ids)
        self.assertFalse(error_b.build_error_link_ids)
        self.assertFalse(error_b.build_ids)

        error_content_c = self.BuildErrorContent.create({'content': 'foo foo'})

        # let's ensure we cannot relink errors with different fingerprints
        with self.assertRaises(AssertionError):
            (error_content_a | error_content_c)._relink()

        # relink two build errors while the build <--> build_error relation already exists
        error_content_d = self.BuildErrorContent.create({'content': 'foo bar'})
        self.BuildErrorLink.create({'build_id': build_a.id, 'error_content_id': error_content_d.id})
        (error_content_a | error_content_d)._relink()
        self.assertIn(build_a, error_content_a.build_error_link_ids.build_id)
        self.assertIn(build_a, error_content_a.build_ids)
        self.assertFalse(error_content_d.build_error_link_ids)
        self.assertFalse(error_content_d.build_ids)

    def test_relink_simple(self):
        build_a = self.create_test_build({'local_result': 'ko', 'local_state': 'done'})
        error_content_a = self.BuildErrorContent.create({'content': 'foo bar'})
        error_a = error_content_a.error_id
        error_a.active = False
        self.BuildErrorLink.create({'build_id': build_a.id, 'error_content_id': error_content_a.id})
        build_b = self.create_test_build({'local_result': 'ko', 'local_state': 'done'})
        error_content_b = self.BuildErrorContent.create({'content': 'foo bar'})
        error_b = error_content_b.error_id
        error_b.test_tags = 'footag'
        self.BuildErrorLink.create({'build_id': build_b.id, 'error_content_id': error_content_b.id})

        self.assertEqual(self.BuildErrorContent.search([('fingerprint', '=', error_content_a.fingerprint)]), error_content_a | error_content_b)
        (error_content_a | error_content_b)._relink()
        self.assertEqual(self.BuildErrorContent.search([('fingerprint', '=', error_content_a.fingerprint)]), error_content_a)
        self.assertFalse(error_b.error_content_ids)

        self.assertTrue(error_a.active, 'The merged error without test tags should have been deactivated')
        self.assertEqual(error_a.test_tags, 'footag', 'Tags should have been transfered from b to a')
        self.assertFalse(error_b.active, 'The merged error with test tags should remain active')
        self.assertIn(build_a, error_content_a.build_ids)
        self.assertIn(build_b, error_content_a.build_ids)
        self.assertFalse(error_content_b.build_ids)
        self.assertEqual(error_a.active, True)

        tagged_error_content = self.BuildErrorContent.create({'content': 'foo bar'})
        tagged_error = tagged_error_content.error_id
        tagged_error.test_tags = 'bartag'
        (error_content_a | tagged_error_content)._relink()
        self.assertEqual(error_a.test_tags, 'footag')
        self.assertEqual(tagged_error.test_tags, 'bartag')
        self.assertTrue(error_a.active)
        self.assertTrue(tagged_error.active, 'A differently tagged error cannot be deactivated by the merge')

    def test_relink_linked(self):
        build_a = self.create_test_build({'local_result': 'ko', 'local_state': 'done'})
        error_content_a = self.BuildErrorContent.create({'content': 'foo bar'})
        error_a = error_content_a.error_id
        error_a.active = False
        self.BuildErrorLink.create({'build_id': build_a.id, 'error_content_id': error_content_a.id})
        build_b = self.create_test_build({'local_result': 'ko', 'local_state': 'done'})
        error_content_b = self.BuildErrorContent.create({'content': 'foo bar'})
        error_b = error_content_b.error_id
        error_b.test_tags = 'footag'
        self.BuildErrorLink.create({'build_id': build_b.id, 'error_content_id': error_content_b.id})

        linked_error = self.BuildErrorContent.create({'content': 'foo foo bar', 'error_id': error_b.id})

        self.assertEqual(self.BuildErrorContent.search([('fingerprint', '=', error_content_a.fingerprint)]), error_content_a | error_content_b)
        (error_content_a | error_content_b)._relink()
        self.assertEqual(self.BuildErrorContent.search([('fingerprint', '=', error_content_a.fingerprint)]), error_content_a)
        self.assertEqual(error_b.error_content_ids, linked_error)
        self.assertTrue(error_a.active, 'Main error should have been reactivated')
        self.assertEqual(error_a.test_tags, False, 'Tags should remain on b')
        self.assertEqual(error_b.test_tags, 'footag', 'Tags should remain on b')
        self.assertTrue(error_b.active, 'The merged error with test tags should remain active')
        self.assertIn(build_a, error_content_a.build_ids)
        self.assertIn(build_b, error_content_a.build_ids)
        self.assertFalse(error_content_b.build_ids)
        self.assertEqual(error_a.active, True)
        self.assertEqual(linked_error.error_id, error_b)

    def test_build_scan(self):
        ko_build = self.create_test_build({'local_result': 'ok', 'local_state': 'testing'})
        ko_build_b = self.create_test_build({'local_result': 'ok', 'local_state': 'testing'})
        ok_build = self.create_test_build({'local_result': 'ok', 'local_state': 'running'})

        self.env['runbot.error.regex'].create({
            'regex': '^FAIL: ',
            're_type': 'cleaning',
        })

        self.env['runbot.error.regex'].create({
            'regex': r'\s*\^+',
            're_type': 'cleaning',
            'replacement': "''",
        })

        error_team = self.RunbotTeam.create({
            'name': 'test-error-team',
            'path_glob': '*/test_ui.py'
        })

        # Test the build parse and ensure that an 'ok' build is not parsed
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 00:46:21'), 'message': RTE_ERROR, 'build_id': ko_build.id})
        # As it happens that a same error could appear again in the same build, ensure that the parsing adds only one link
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 00:48:21'), 'message': RTE_ERROR, 'build_id': ko_build.id})

        # now simulate another build with the same errors
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 01:46:21'), 'message': RTE_ERROR, 'build_id': ko_build_b.id})
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 01:46:21'), 'message': RTE_ERROR, 'build_id': ko_build_b.id})

        # The error also appears in a running build
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 01:46:21'), 'message': RTE_ERROR, 'build_id': ok_build.id})

        self.assertEqual(ko_build.local_result, 'ko', 'Testing build should have gone ko after error log')
        self.assertEqual(ok_build.local_result, 'ok', 'Running build should not have gone ko after error log')

        ko_build._parse_logs()
        ko_build_b._parse_logs()
        ok_build._parse_logs()
        build_error = ko_build.build_error_ids
        self.assertTrue(build_error)
        error_content = build_error.error_content_ids
        self.assertTrue(error_content.fingerprint.startswith('af0e88f3'))
        self.assertTrue(error_content.cleaned_content.startswith('%'), 'The cleaner should have replace "FAIL: " with a "%" sign by default')
        self.assertFalse('^' in error_content.cleaned_content, 'The cleaner should have removed the "^" chars')
        error_link = self.env['runbot.build.error.link'].search([('build_id', '=', ko_build.id), ('error_content_id', '=', error_content.id)])
        self.assertTrue(error_link, 'An error link should exists')
        self.assertIn(ko_build, error_content.build_ids, 'Ko build should be in build_error_link_ids')
        self.assertEqual(error_link.log_date, fields.Datetime.from_string('2023-08-29 00:46:21'))
        self.assertIn(ko_build, error_content.build_ids, 'The parsed build should be added to the runbot.build.error')
        self.assertFalse(self.BuildErrorLink.search([('build_id', '=', ok_build.id)]), 'A successful build should not be associated to a runbot.build.error')
        self.assertEqual(error_content.file_path, '/data/build/server/addons/web_studio/tests/test_ui.py')
        self.assertEqual(build_error.team_id, error_team)

        # Test that build with same error is added to the errors
        ko_build_same_error = self.create_test_build({'local_result': 'ko'})
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 01:46:21'), 'message': RTE_ERROR, 'build_id': ko_build_same_error.id})
        ko_build_same_error._parse_logs()
        self.assertIn(ko_build_same_error, error_content.build_ids, 'The parsed build should be added to the existing runbot.build.error')

        # Test that line numbers does not interfere with error recognition
        ko_build_diff_number = self.create_test_build({'local_result': 'ko'})
        rte_diff_numbers = RTE_ERROR.replace('89', '100').replace('1062', '1000').replace('1046', '4610')
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 01:46:21'), 'message': rte_diff_numbers, 'build_id': ko_build_diff_number.id})
        ko_build_diff_number._parse_logs()
        self.assertIn(ko_build_diff_number, build_error.build_ids, 'The parsed build with different line numbers in error should be added to the runbot.build.error')

        # Test that when an error re-appears after the bug has been fixed,
        # a new build error is created, with the old one linked
        build_error.active = False
        ko_build_new = self.create_test_build({'local_result': 'ko'})
        self.create_log({'create_date': fields.Datetime.from_string('2023-08-29 01:46:21'), 'message': RTE_ERROR, 'build_id': ko_build_new.id})
        ko_build_new._parse_logs()
        self.assertNotIn(ko_build_new, build_error.build_ids, 'The parsed build should not be added to a fixed runbot.build.error')
        new_build_error = self.BuildErrorLink.search([('build_id', '=', ko_build_new.id)]).error_content_id.error_id
        self.assertIn(ko_build_new, new_build_error.build_ids, 'The parsed build with a re-apearing error should generate a new runbot.build.error')
        self.assertEqual(build_error, new_build_error.previous_error_id, 'The old error should appear in history')

    def test_seen_date(self):
        # create all the records before the tests to evaluate compute dependencies
        build_a = self.create_test_build({'local_result': 'ok', 'local_state': 'testing'})
        first_seen_date = fields.Datetime.from_string('2023-08-29 00:46:21')
        self.create_log({'create_date': first_seen_date, 'message': RTE_ERROR, 'build_id': build_a.id})

        build_b = self.create_test_build({'local_result': 'ok', 'local_state': 'testing'})
        new_seen_date = fields.Datetime.from_string('2023-08-29 02:46:21')
        self.create_log({'create_date': new_seen_date, 'message': RTE_ERROR, 'build_id': build_b.id})

        build_c = self.create_test_build({'local_result': 'ok', 'local_state': 'testing'})
        child_seen_date = fields.Datetime.from_string('2023-09-01 12:00:00')
        self.create_log({'create_date': child_seen_date, 'message': 'Fail: foo bar error', 'build_id': build_c.id})

        build_d = self.create_test_build({'local_result': 'ok', 'local_state': 'testing'})
        new_child_seen_date = fields.Datetime.from_string('2023-09-02 12:00:00')
        self.create_log({'create_date': new_child_seen_date, 'message': 'Fail: foo bar error', 'build_id': build_d.id})

        build_a._parse_logs()
        build_error_a = build_a.build_error_ids
        self.assertEqual(build_error_a.first_seen_date, first_seen_date)
        self.assertEqual(build_error_a.first_seen_build_id, build_a)
        self.assertEqual(build_error_a.last_seen_date, first_seen_date)
        self.assertEqual(build_error_a.last_seen_build_id, build_a)

        # a new build with the same error should be the last seen
        build_b._parse_logs()
        self.assertEqual(build_error_a.last_seen_date, new_seen_date)
        self.assertEqual(build_error_a.last_seen_build_id, build_b)

        # a new build error is linked to the current one
        build_c._parse_logs()
        build_error_c = build_c.build_error_ids
        self.assertNotIn(build_c, build_error_a.build_ids)
        build_error_a._merge(build_error_c)
        self.assertIn(build_c, build_error_a.build_ids)
        self.assertEqual(build_error_a.last_seen_date, child_seen_date)
        self.assertEqual(build_error_a.last_seen_build_id, build_c)

        # a new build appears in the linked error
        build_d._parse_logs()
        self.assertEqual(build_error_a.last_seen_date, new_child_seen_date)
        self.assertEqual(build_error_a.last_seen_build_id, build_d)

    def test_build_error_links(self):
        build_a = self.create_test_build({'local_result': 'ko'})
        build_b = self.create_test_build({'local_result': 'ko'})

        error_content_a = self.env['runbot.build.error.content'].create({
            'content': 'foo',
        })

        self.BuildErrorLink.create({'build_id': build_a.id, 'error_content_id': error_content_a.id})
        error_content_b = self.env['runbot.build.error.content'].create({
            'content': 'bar',
            'random': True
        })
        self.BuildErrorLink.create({'build_id': build_b.id, 'error_content_id': error_content_b.id})

        #  test that the random bug is parent when linking errors
        self.assertNotEqual(error_content_a.error_id, error_content_b.error_id)
        all_errors = error_content_a | error_content_b
        all_errors.action_link_errors_contents()
        self.assertEqual(error_content_a.error_id, error_content_b.error_id, 'Error should be linked')

        #  Test build_ids
        self.assertEqual(build_a, error_content_a.build_ids)
        self.assertEqual(build_b, error_content_b.build_ids)
        error = error_content_a.error_id
        self.assertEqual(build_a | build_b, error.build_ids)

    def test_build_error_test_tags_no_version(self):
        build_a = self.create_test_build({'local_result': 'ko'})
        build_b = self.create_test_build({'local_result': 'ko'})

        error_a = self.BuildError.create({
            'content': 'foo',
            'build_ids': [(6, 0, [build_a.id])],
            'random': True,
            'active': True,
            'test_tags': 'foo,bar',
        })

        error_b = self.BuildError.create({
            'content': 'bar',
            'build_ids': [(6, 0, [build_b.id])],
            'random': True,
            'active': False,
            'test_tags': 'blah',
        })

        self.assertIn('-foo', self.BuildError._disabling_tags())
        self.assertIn('-bar', self.BuildError._disabling_tags())

        # test that test tags on fixed errors are not taken into account
        self.assertNotIn('-blah', self.BuildError._disabling_tags())

    def test_build_error_test_tags_min_max_version(self):
        version_17 = self.Version.create({'name': '17.0'})
        version_saas_171 = self.Version.create({'name': 'saas-17.1'})
        version_master = self.Version.create({'name': 'master'})

        build_v13 = self.create_test_build({'local_result': 'ko'})
        build_v17 = self.create_test_build({'local_result': 'ko', 'params_id': self.create_params({'version_id': version_17.id}).id})
        build_saas_171 = self.create_test_build({'local_result': 'ko', 'params_id': self.create_params({'version_id': version_saas_171.id}).id})
        build_master = self.create_test_build({'local_result': 'ko', 'params_id': self.create_params({'version_id': version_master.id}).id})

        self.BuildError.create(
            [
                {
                    "content": "foobar",
                    "build_ids": [(6, 0, [build_v13.id])],
                    "test_tags": "every,where",
                },
                {
                    "content": "blah",
                    "build_ids": [(6, 0, [build_v17.id])],
                    "test_tags": "tag_17_up_to_master",
                    "tags_min_version_id": version_17.id,
                },
                {
                    "content": "spam",
                    "build_ids": [(6, 0, [build_v17.id])],
                    "test_tags": "tag_up_to_17",
                    "tags_max_version_id": version_17.id,
                },
                {
                    "content": "eggs",
                    "build_ids": [(6, 0, [build_saas_171.id])],
                    "test_tags": "tag_only_17.1",
                    "tags_min_version_id": version_saas_171.id,
                    "tags_max_version_id": version_saas_171.id,
                },
            ]
        )

        self.assertEqual(sorted(['-every', '-where', '-tag_17_up_to_master', '-tag_up_to_17', '-tag_only_17.1']), sorted(self.BuildError._disabling_tags()), "Should return the whole list without parameters")
        self.assertEqual(sorted(['-every', '-where', '-tag_up_to_17']), sorted(self.BuildError._disabling_tags(build_v13)))
        self.assertEqual(sorted(['-every', '-where', '-tag_up_to_17', '-tag_17_up_to_master']), sorted(self.BuildError._disabling_tags(build_v17)))
        self.assertEqual(sorted(['-every', '-where', '-tag_17_up_to_master', '-tag_only_17.1']), sorted(self.BuildError._disabling_tags(build_saas_171)))
        self.assertEqual(sorted(['-every', '-where', '-tag_17_up_to_master']), sorted(self.BuildError._disabling_tags(build_master)))

    def test_build_error_team_wildcards(self):
        website_team = self.RunbotTeam.create({
            'name': 'website_test',
            'path_glob': '*website*,-*website_sale*'
        })

        self.assertTrue(website_team.dashboard_id.exists())
        teams = self.env['runbot.team'].search(['|', ('path_glob', '!=', False), ('module_ownership_ids', '!=', False)])
        self.assertFalse(teams._get_team('/data/build/odoo/addons/web_studio/tests/test_ui.py'))
        self.assertFalse(teams._get_team('/data/build/enterprise/website_sale/tests/test_sale_process.py'))
        self.assertEqual(website_team, teams._get_team('/data/build/odoo/addons/website_crm/tests/test_website_crm'))
        self.assertEqual(website_team, teams._get_team('/data/build/odoo/addons/website/tests/test_ui'))

    def test_build_error_team_ownership(self):
        website_team = self.RunbotTeam.create({
            'name': 'website_test',
            'path_glob': ''
        })
        sale_team = self.RunbotTeam.create({
            'name': 'sale_test',
            'path_glob': ''
        })
        module_website = self.env['runbot.module'].create({
            'name': 'website_crm'
        })
        module_sale = self.env['runbot.module'].create({
            'name': 'website_sale'
        })
        self.env['runbot.module.ownership'].create({'module_id': module_website.id, 'team_id': website_team.id, 'is_fallback': True})
        self.env['runbot.module.ownership'].create({'module_id': module_sale.id, 'team_id': sale_team.id, 'is_fallback': False})
        self.env['runbot.module.ownership'].create({'module_id': module_sale.id, 'team_id': website_team.id, 'is_fallback': True})

        self.repo_server.name = 'odoo'
        self.repo_addons.name = 'enterprise'
        teams = self.env['runbot.team'].search(['|', ('path_glob', '!=', False), ('module_ownership_ids', '!=', False)])
        self.assertFalse(teams._get_team('/data/build/odoo/addons/web_studio/tests/test_ui.py'))
        self.assertEqual(website_team, teams._get_team('/data/build/odoo/addons/website_crm/tests/test_website_crm'))
        self.assertEqual(sale_team, teams._get_team('/data/build/enterprise/website_sale/tests/test_sale_process.py'))

    def test_dashboard_tile_simple(self):
        self.additionnal_setup()
        bundle = self.env['runbot.bundle'].search([('project_id', '=', self.project.id)])
        bundle.last_batch.state = 'done'
        bundle._compute_last_done_batch()  # force the recompute
        self.assertTrue(bool(bundle.last_done_batch.exists()))
        # simulate a failed build that we want to monitor
        failed_build = bundle.last_done_batch.slot_ids[0].build_id
        failed_build.global_result = 'ko'
        failed_build.flush_recordset()

        team = self.env['runbot.team'].create({'name': 'Test team'})
        dashboard = self.env['runbot.dashboard.tile'].create({
            'project_id': self.project.id,
            'category_id': bundle.last_done_batch.category_id.id,
        })

        self.assertEqual(dashboard.build_ids, failed_build)

class TestCodeOwner(RunbotCase):

    def setUp(self):
        super().setUp()
        self.cow_deb = self.env['runbot.codeowner'].create({
            'project_id' : self.project.id,
            'github_teams': 'runbot',
            'regex': '.*debian.*'
        })

        self.cow_web = self.env['runbot.codeowner'].create({
            'project_id' : self.project.id,
            'github_teams': 'website',
            'regex': '.*website.*'
        })

        self.cow_crm = self.env['runbot.codeowner'].create({
            'project_id' : self.project.id,
            'github_teams': 'crm',
            'regex': '.*crm.*'
        })

        self.cow_all = self.cow_deb | self.cow_web | self.cow_crm

    def test_codeowner_invalid_regex(self):
        with self.assertRaises(ValidationError):
            self.env['runbot.codeowner'].create({
                'project_id': self.project.id,
                'regex': '*debian.*',
                'github_teams': 'rd-test'
            })
