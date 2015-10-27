from argparse import Namespace
import mock
from mock import patch
import unittest

from bundletester import builder
from bundletester import config


class O(object):
    pass


class TestBuilder(unittest.TestCase):

    @mock.patch('subprocess.check_call')
    def test_builder_virtualenv(self, mcall):
        parser = config.Parser()
        b = builder.Builder(parser, None)
        b.build_virtualenv('venv')
        self.assertEqual(mcall.call_args[0][0], ['virtualenv', 'venv'])

    @mock.patch('subprocess.check_call')
    def test_builder_sources(self, mcall):
        parser = config.Parser()
        b = builder.Builder(parser, None)

        parser.sources.append('ppa:foo')
        b.add_sources(False)
        self.assertEqual(mcall.call_args,
                         mock.call(['sudo', 'apt-add-repository',
                                    '--yes', 'ppa:foo']))

    @mock.patch('subprocess.check_call')
    def test_builder_packages(self, mcall):
        parser = config.Parser()
        b = builder.Builder(parser,  None)
        parser.packages.extend(['a', 'b'])
        b.install_packages()
        self.assertEqual(mcall.call_args,
                         mock.call(['sudo', 'apt-get', 'install', '-qq', '-y',
                                    'a', 'b']))

    @mock.patch('subprocess.call')
    def test_builder_bootstrap_dryrun(self, mcall):
        parser = config.Parser()
        f = O()
        f.dryrun = True
        f.environment = 'local'
        b = builder.Builder(parser, f)
        b.bootstrap()
        self.assertFalse(mcall.called)

    def test_full_args(self):
        class options:
            environment='foo'
        parser = config.Parser()
        b = builder.Builder(parser,  options)
        full = b._full_args('bar', ('baz', 'qux'))
        self.assertEqual(('juju', '--show-log', 'bar', '-e', 'foo', 'baz',
                          'qux'), full)
        full = b._full_args('bar', ('baz', 'qux'))
        self.assertEqual((
            'juju', '--show-log', 'bar', '-e', 'foo',
            'baz', 'qux'), full)
        b.env_name = None
        full = b._full_args('bar', ('baz', 'qux'))
        self.assertEqual(('juju', '--show-log', 'bar', 'baz', 'qux'), full)

    def test_full_args_action(self):
        class options:
            environment='foo'
        parser = config.Parser()
        b = builder.Builder(parser,  options)
        full = b._full_args('action bar', ('baz', 'qux'))
        self.assertEqual((
            'juju', '--show-log', 'action', 'bar', '-e', 'foo', 'baz', 'qux'),
            full)

    def test_get_juju_output(self):

        def asdf(x, stderr):
            return 'asdf'

        client = self.create_builder()
        with patch('subprocess.check_output', side_effect=asdf) as mock:
            result = client.get_juju_output('bar')
        self.assertEqual('asdf', result)
        self.assertEqual((('juju', '--show-log', 'bar', '-e', 'foo'),),
                         mock.call_args[0])

    def test_get_juju_output_accepts_varargs(self):

        def asdf(x, stderr):
            return 'asdf'

        b = self.create_builder()
        with patch('subprocess.check_output', side_effect=asdf) as mock:
            result = b.get_juju_output('bar', 'baz', '--qux')
        self.assertEqual('asdf', result)
        self.assertEqual((('juju', '--show-log', 'bar', '-e', 'foo', 'baz',
                           '--qux'),), mock.call_args[0])

    def test_action_do(self):
        b = self.create_builder()
        with patch.object(b, 'get_juju_output') as mock:
            mock.return_value = \
                "Action queued with id: 5a92ec93-d4be-4399-82dc-7431dbfd08f9"
            id = b.action_do("foo/0", "myaction", "param=5")
            self.assertEqual(id, "5a92ec93-d4be-4399-82dc-7431dbfd08f9")
        mock.assert_called_once_with(
            'action do', 'foo/0', 'myaction', "param=5"
        )

    def test_action_do_error(self):
        b = self.create_builder()
        with patch.object(b, 'get_juju_output') as mock:
            mock.return_value = "some bad text"
            with self.assertRaisesRegexp(Exception,
                                         "Action id not found in output"):
                b.action_do("foo/0", "myaction", "param=5")

    def test_action_fetch(self):
        b = self.create_builder()
        with patch.object(b, 'get_juju_output') as mock:
            ret = "status: completed\nfoo: bar"
            mock.return_value = ret
            out = b.action_fetch("123")
            self.assertEqual(out, ret)
        mock.assert_called_once_with(
            'action fetch', '123', "--wait", "1m"
        )

    def test_action_do_fetch(self):
        b = self.create_builder()
        with patch.object(b, 'get_juju_output') as mock:
            ret = "status: completed\nfoo: bar"
            # setting side_effect to an iterable will return the next value
            # from the list each time the function is called.
            mock.side_effect = [
                "Action queued with id: 5a92ec93-d4be-4399-82dc-7431dbfd08f9",
                ret]
            out = b.action_do_fetch("foo/0", "myaction", "param=5")
            self.assertEqual(out, ret)

    def create_builder(self):
        class options:
            environment='foo'
        parser = config.Parser()
        return builder.Builder(parser,  options)
