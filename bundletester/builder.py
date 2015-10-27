import logging
import os
import re
import subprocess
import tempfile
import time

import websocket
from deployer.env.go import GoEnvironment

from utils import yaml_loads


class Builder(object):
    """Build out the system-level environment needed to run tests"""

    def __init__(self, config, options, debug=False):
        self.config = config
        self.options = options
        self.environment = None
        self.env_name = None
        if options:
            self.env_name = options.environment
            if self.env_name:
                self.environment = GoEnvironment(self.env_name)
        self.debug = debug

    def bootstrap(self):
        if not self.environment:
            return
        logging.debug("Bootstrap environment: %s" % self.env_name)
        if self.options.dryrun:
            return
        ec = subprocess.call(['juju', 'status', '-e', self.env_name],
                             stdout=open('/dev/null', 'w'),
                             stderr=subprocess.STDOUT)

        if ec != 0:
            if self.config.bootstrap is True:
                logging.info("Bootstrapping Juju Environment...")
                self.environment.bootstrap()
                self.environment.connect()
                return True
        else:
            self.environment.connect()

    def deploy(self, bundle):
        result = {
            'returncode': 0
        }
        bundle = bundle or self.options.bundle
        if not bundle:
            return result
        if not os.path.exists(bundle):
            raise OSError("Missing required bundle file: %s" % bundle)
        if self.options.dryrun:
            return result
        cmd = ['juju-deployer']
        if self.options.verbose:
            cmd.append('-Wvd')
        cmd += ['-c', bundle]
        if self.options.deployment:
            cmd.append(self.options.deployment)

        logging.debug("deploy %s", ' '.join(cmd))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)

        # Print all output as it comes in to debug
        output = []
        lines = iter(p.stdout.readline, "")
        for line in lines:
            output.append(line)
            logging.debug(str(line.rstrip()))

        p.communicate()
        return {
            'returncode': p.returncode,
            'output': ''.join(output),
            'executable': cmd
        }

    def destroy(self):
        if self.options.no_destroy is not True:
            subprocess.check_call(['juju', 'destroy-environment',
                                   '-y', self.env_name, '--force'])

    def reset(self):
        if self.options.dryrun:
            return
        if self.environment:
            start, timeout = time.time(), 60
            while True:
                try:
                    self.environment.reset(
                        terminate_machines=True,
                        terminate_delay=60,
                        force_terminate=True
                    )
                    break
                except Exception as e:
                    logging.exception(e)

                    if isinstance(
                            e, websocket.WebSocketConnectionClosedException):
                        logging.debug('Reconnectinng to environment...')
                        self.environment.connect()
                        continue

                    if (time.time() - start) > timeout:
                        raise RuntimeError(
                            'Timeout exceeded. Failed to reset environment '
                            ' in %s seconds.' % timeout)
                    time.sleep(1)
                    logging.debug('Retrying environment reset...')

            # wait for all services to be removed
            logging.debug("Waiting for services to be removed...")
            start, timeout = time.time(), 60
            while True:
                status = self.environment.status()
                if not status.get('services', {}):
                    break
                if (time.time() - start) > timeout:
                    raise RuntimeError(
                        'Timeout exceeded. Failed to destroy all services '
                        ' in %s seconds.' % timeout)
                logging.debug(
                    " Remaining services: %s", status.get("services").keys())
                time.sleep(4)

    def build_virtualenv(self, path):
        subprocess.check_call(['virtualenv', path],
                              stdout=open('/dev/null', 'w'))

    def add_source(self, source):
        subprocess.check_call(['sudo', 'apt-add-repository', '--yes', source])

    def add_sources(self, update=True):
        for source in self.config.sources:
            self.add_source(source)
        if self.config.sources and update:
            self.apt_update()

    def apt_update(self):
        subprocess.check_call(['sudo', 'apt-get', 'update', '-qq'])

    def install_packages(self):
        if not self.config.packages:
            return
        cmd = ['sudo', 'apt-get', 'install', '-qq', '-y']
        cmd.extend(self.config.packages)
        subprocess.check_call(cmd)

    def _full_args(self, command, args, timeout=None, include_e=True):
        if self.env_name is None or not include_e:
            e_arg = ()
        else:
            e_arg = ('-e', self.env_name)
        if timeout is None:
            prefix = ()
        else:
            prefix = ('timeout', timeout)
        logging = '--debug' if self.debug else '--show-log'

        # we split the command here so that the caller can control where the -e
        # <env> flag goes.  Everything in the command string is put before the
        # -e flag.
        command = command.split()
        return prefix + ('juju', logging,) + tuple(command) + e_arg + args

    def get_juju_output(self, command, *args, **kwargs):
        """Call a juju command and return the output.

        Sub process will be called as 'juju <command> <args> <kwargs>'. Note
        that <command> may be a space delimited list of arguments. The -e
        <environment> flag will be placed after <command> and before args.
        """
        args = self._full_args(command, args,
                               timeout=kwargs.get('timeout'),
                               include_e=kwargs.get('include_e', True))
        with tempfile.TemporaryFile() as stderr:
            try:
                sub_output = subprocess.check_output(args, stderr=stderr)
                return sub_output
            except subprocess.CalledProcessError as e:
                stderr.seek(0)
                e.stderr = stderr.read()
                if ('Unable to connect to environment' in e.stderr or
                        'MissingOrIncorrectVersionHeader' in e.stderr or
                        '307: Temporary Redirect' in e.stderr):
                    raise CannotConnectEnv(e)
                raise

    def action_fetch(self, id, action=None, timeout="1m"):
        """Fetches the results of the action with the given id.

        Will wait for up to 1 minute for the action results.
        The action name here is just used for an more informational error in
        cases where it's available.
        Returns the yaml output of the fetched action.
        """
        out = self.get_juju_output("action fetch", id, "--wait", timeout)
        status = yaml_loads(out)["status"]
        if status != "completed":
            name = ""
            if action is not None:
                name = " " + action
            raise Exception(
                "timed out waiting for action%s to complete during fetch" %
                name)
        return out

    def action_do(self, unit, action, *args):
        """Performs the given action on the given unit.

        Action params should be given as args in the form foo=bar.
        Returns the id of the queued action.
        """
        args = (unit, action) + args
        output = self.get_juju_output("action do", *args)
        action_id_pattern = re.compile(
            'Action queued with id: ([a-f0-9\-]{36})')
        match = action_id_pattern.search(output)
        if match is None:
            raise Exception("Action id not found in output: %s" %
                            output)
        return match.group(1)

    def action_do_fetch(self, unit, action, timeout="1m", *args):
        """Performs given action on given unit and waits for the results.

        Action params should be given as args in the form foo=bar.
        Returns the yaml output of the action.
        """
        id = self.action_do(unit, action, *args)
        return self.action_fetch(id, action, timeout)


class CannotConnectEnv(subprocess.CalledProcessError):

    def __init__(self, e):
        super(CannotConnectEnv, self).__init__(e.returncode, e.cmd, e.output)
