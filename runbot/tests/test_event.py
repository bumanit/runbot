# -*- coding: utf-8 -*-
from ..common import markdown_escape, markdown_unescape
from .common import RunbotCase


class TestIrLogging(RunbotCase):

    def test_markdown(self):
        log = self.env['ir.logging'].create({
            'name': 'odoo.runbot',
            'type': 'markdown',
            'path': 'runbot',
            'level': 'INFO',
            'line': 0,
            'func': 'test_markdown',
            'message': 'some **bold text** and also some __underlined text__ and maybe a bit of ~~strikethrough text~~'
        })

        self.assertEqual(
            log._markdown(),
            'some <strong>bold text</strong> and also some <ins>underlined text</ins> and maybe a bit of <del>strikethrough text</del>'
        )

        #log.message = 'a bit of code `import foo\nfoo.bar`'
        #self.assertEqual(
        #    log._markdown(),
        #    'a bit of code <code>import foo\nfoo.bar</code>'
        #)

        log.message = '`import foo`'
        self.assertEqual(
            str(log._markdown()),
            '<code>import foo</code>',
        )

        log.message = 'a bit of code :\n`import foo`'
        self.assertEqual(
            str(log._markdown()),
            'a bit of code :<br/>\n<code>import foo</code>',
        )


        # test icon
        log.message = 'Hello @icon-file-text-o'
        self.assertEqual(
            log._markdown(),
            'Hello <i class="fa fa-file-text-o"></i>'
        )

        log.message = 'a bit of code :\n`print(__name__)`'
        self.assertEqual(
            log._markdown(),
            'a bit of code :<br/>\n<code>print(__name__)</code>'
        )

        log.message = 'a bit of __code__ :\n`print(__name__)` **but also** `print(__name__)`'
        self.assertEqual(
            log._markdown(),
            'a bit of <ins>code</ins> :<br/>\n<code>print(__name__)</code> <strong>but also</strong> <code>print(__name__)</code>'
        )


        # test links
        log.message = 'This [link](https://wwww.somewhere.com) goes to somewhere and [this one](http://www.nowhere.com) to nowhere.'
        self.assertEqual(
            str(log._markdown()),
            'This <a href="https://wwww.somewhere.com">link</a> goes to somewhere and <a href="http://www.nowhere.com">this one</a> to nowhere.'
        )

        # test link with icon
        log.message = '[@icon-download](https://wwww.somewhere.com) goes to somewhere.'
        self.assertEqual(
            log._markdown(),
            '<a href="https://wwww.somewhere.com"><i class="fa fa-download"></i></a> goes to somewhere.'
        )

        # test links with icon and text
        log.message = 'This [link@icon-download](https://wwww.somewhere.com) goes to somewhere.'
        self.assertEqual(
            log._markdown(),
            'This <a href="https://wwww.somewhere.com">link<i class="fa fa-download"></i></a> goes to somewhere.'
        )

        # test sanitization
        log.message = 'foo <script>console.log("hello world")</script>'
        self.assertEqual(
            log._markdown(),
            'foo &lt;script&gt;console.log(&#34;hello world&#34;)&lt;/script&gt;'
        )

        log.message = f'[file]({markdown_escape("https://repo/file/__init__.py")})'
        self.assertEqual(
            str(log._markdown()),
            '<a href="https://repo/file/__init__.py">file</a>',
        )

        # BEHAVIOUR TO DEFINE

        log.message = f'[__underline text__]({markdown_escape("https://repo/file/__init__.py")})'
        self.assertEqual(
            str(log._markdown()),
            '<a href="https://repo/file/__init__.py"><ins>underline text</ins></a>',
        )

        # BEHAVIOUR TO DEFINE
        log.message = f'[{markdown_escape("__init__.py")}]({markdown_escape("https://repo/file/__init__.py")})'
        self.assertEqual(
            str(log._markdown()),
            '<a href="https://repo/file/__init__.py">__init__.py</a>',
        )

        log.message = f'''This is a list of failures in some files:
[{markdown_escape("__init__.py")}]({markdown_escape("https://repo/file/__init__.py")})
`{markdown_escape("Some code with talking about __enter__")}`
[{markdown_escape("__init__.py")}]({markdown_escape("https://repo/file/__init__.py")})
`{markdown_escape("Some code with `code block` inside")}`'''

        self.assertEqual(
            str(log._markdown()),
            '''This is a list of failures in some files:<br/>
<a href="https://repo/file/__init__.py">__init__.py</a><br/>
<code>Some code with talking about __enter__</code><br/>
<a href="https://repo/file/__init__.py">__init__.py</a><br/>
<code>Some code with `code block` inside</code>''')

        for code in [
            'leading\\',
            'leading\\\\',
            '`',
            '\\`',
            '\\``',
            '``',
            '`\n`',
            ]:
            escaped_code = markdown_escape(code)

            log.message = f'This is a bloc code `{escaped_code}`'
            self.assertEqual(
                str(log._markdown()),
                f'This is a bloc code <code>{code}</code>',
            )

    def test_build_log_markdown(self):
        build = self.env['runbot.build'].create({'params_id': self.base_params.id})

        def last_log():
            return str(build.log_ids[-1]._markdown())

        name = '__init__.py'
        url = '/path/to/__init__.py'
        code = '# comment `for` something'
        build._log('f', 'Some message [%s](%s) \n `%s`', name, url, code, log_type='markdown')
        self.assertEqual(last_log(), f'Some message <a href="{url}">{name}</a> <br/>\n <code>{code}</code>')

        name = '[__init__.py]'
        url = '/path/to/__init__.py=(test))'
        code = '# comment `for` something\\'
        build._log('f', 'Some message [%s](%s) \n `%s`', name, url, code, log_type='markdown')
        self.assertEqual(last_log(), f'Some message <a href="{url}">{name}</a> <br/>\n <code>{code}</code>')
