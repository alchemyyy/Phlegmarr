import sys
import os
import argparse
import yaml
import requests
from urllib3.util import Retry


class Api(object):

    def __init__(self, base_url, apikey=None, apiroot=None):
        self.base_url = base_url.rstrip('/')
        self.path = apiroot if apiroot else ''
        self.apikey = apikey
        self.errors = 0

        adapter = requests.adapters.HTTPAdapter(max_retries=Retry(total=10, backoff_factor=0.1))

        self.r = requests.Session()
        self.r.mount('http://', adapter)
        self.r.mount('https://', adapter)

        if self.apikey:
            self.r.headers.update({'X-Api-Key': self.apikey})

    def __url(self, resource='', id=None):
        id_path = '/{}'.format(id) if id else ''
        return '{}{}{}{}'.format(self.base_url, self.path, resource, id_path)

    def __log_error(self, action, resource, status_code, response):
        self.errors += 1
        try:
            body = response.json()
            message = body.get('message', body.get('error', str(body)))
        except Exception:
            message = response.text[:200] if response.text else '(no response body)'
        print('  ERROR {} {}: HTTP {} - {}'.format(action, resource, status_code, message))

    def __get(self, resource, id=None):
        url = self.__url(resource, id)
        try:
            response = self.r.get(url)
        except requests.exceptions.ConnectionError as e:
            self.errors += 1
            print('  ERROR connecting to {}: {}'.format(url, e))
            return None

        status_code = response.status_code
        id_string = ' {}'.format(id) if id else ''

        if status_code < 300:
            print('  Fetched {}{}: {}'.format(resource, id_string, status_code))
            return response.json()
        else:
            self.__log_error('fetching', '{}{}'.format(resource, id_string), status_code, response)
            return None

    def __create(self, resource, body):
        try:
            response = self.r.post(self.__url(resource), json=body)
        except requests.exceptions.ConnectionError as e:
            self.errors += 1
            print('  ERROR connecting for create {}: {}'.format(resource, e))
            return None

        status_code = response.status_code

        if status_code < 300:
            print('  Created {} {}: {}'.format(resource, response.json()['id'], status_code))
            return response.json()
        else:
            # Retry with force if the only errors are warnings
            try:
                errors = response.json()
                if isinstance(errors, list) and all(e.get('isWarning') for e in errors):
                    body['force'] = True
                    response = self.r.post(self.__url(resource), json=body)
                    if response.status_code < 300:
                        print('  Created {} {} (forced past warnings): {}'.format(resource, response.json()['id'], response.status_code))
                        return response.json()
            except Exception:
                pass
            self.__log_error('creating', resource, status_code, response)
            return None

    def __edit(self, resource, body, id=None):
        old_version = self.__get(resource, id)
        if old_version is None:
            return

        for key in body:
            old_version[key] = body[key]

        try:
            response = self.r.put(self.__url(resource, id), json=old_version)
        except requests.exceptions.ConnectionError as e:
            self.errors += 1
            print('  ERROR connecting for edit {}: {}'.format(resource, e))
            return

        status_code = response.status_code
        id_string = ' {}'.format(id) if id else ''

        if status_code < 300:
            print('  Edited {}{}: {}'.format(resource, id_string, status_code))
        else:
            self.__log_error('editing', '{}{}'.format(resource, id_string), status_code, response)

    def __delete(self, resource, id, name=None):
        label = '{} ({})'.format(name, id) if name else str(id)
        try:
            response = self.r.delete(self.__url(resource, id))
        except requests.exceptions.ConnectionError as e:
            self.errors += 1
            print('  ERROR connecting for delete {} {}: {}'.format(resource, label, e))
            return False

        status_code = response.status_code

        if status_code < 300:
            print('  Deleted {} {}: {}'.format(resource, label, status_code))
            return True
        else:
            self.__log_error('deleting', '{} {}'.format(resource, label), status_code, response)
            return False

    def __triage_and_apply(self, object, resource=''):
        if isinstance(object, dict):
            if any(isinstance(object[key], (dict, list)) for key in object):
                for key in object:
                    self.__triage_and_apply(object[key], '{}/{}'.format(resource, key))
            else:
                self.__edit(resource, object)
        else:
            existing = self.__get(resource)
            existing_by_key = {}
            if isinstance(existing, list):
                for item in existing:
                    key = item.get('name') or item.get('path')
                    if key:
                        existing_by_key[key] = item
            for body in object:
                key = body.get('name') or body.get('path')
                if key and key in existing_by_key:
                    self.__delete(resource, existing_by_key[key]['id'], name=key)
                self.__create(resource, body)

    def initialize(self):
        if self.apikey and self.path:
            print('Using provided API key and API root from config')
            return

        url = '{}/initialize.json'.format(self.__url())
        try:
            response = self.r.get(url)
        except requests.exceptions.ConnectionError as e:
            self.errors += 1
            print('ERROR: Could not connect to {}: {}'.format(url, e))
            return

        if response.status_code >= 300:
            self.__log_error('initializing', url, response.status_code, response)
            return

        response_data = response.json()

        if not self.path:
            self.path = response_data["apiRoot"]
        if not self.apikey:
            api_key = response_data["apiKey"]
            self.apikey = api_key
            self.r.headers.update({'X-Api-Key': api_key})

        print('Successfully connected to the server and fetched the API key and path')

    def __strip_cf_scores(self):
        """Zero out all custom format scores in every quality profile so CFs can be deleted."""
        profiles = self.__get('/qualityprofile')
        if not profiles:
            return
        for profile in profiles:
            if profile.get('formatItems'):
                # Zero out scores instead of emptying the array —
                # Radarr requires formatItems to have entries, not be empty
                for item in profile['formatItems']:
                    item['score'] = 0
                try:
                    response = self.r.put(self.__url('/qualityprofile', profile['id']), json=profile)
                except requests.exceptions.ConnectionError as e:
                    self.errors += 1
                    print('  ERROR stripping CF scores from profile {}: {}'.format(profile.get('name'), e))
                    continue
                if response.status_code < 300:
                    print('  Stripped CF scores from profile: {}'.format(profile.get('name')))
                else:
                    self.__log_error('stripping scores from', 'profile {}'.format(profile.get('name')), response.status_code, response)

    def purge(self, resources):
        """Delete all items from each resource endpoint in the list."""
        # If we're about to purge custom formats but NOT quality profiles,
        # strip scores first so CFs aren't "in use". If profiles are also
        # being purged, delete them first instead (no stripping needed).
        if '/customformat' in resources and '/qualityprofile' not in resources:
            print('  Stripping CF scores from all quality profiles...')
            self.__strip_cf_scores()

        # Delete quality profiles before custom formats to avoid dependency issues
        ordered = sorted(resources, key=lambda r: (0 if r == '/qualityprofile' else 1))
        for resource in ordered:
            items = self.__get(resource)
            if not items:
                print('  Nothing to purge from {}'.format(resource))
                continue
            print('  Purging {} items from {}...'.format(len(items), resource))
            failed = []
            for item in items:
                name = item.get('name', None)
                success = self.__delete(resource, item['id'], name=name)
                if not success:
                    failed.append(item)
            if failed:
                # Undo error counts for failed deletes — these will be
                # overwritten by the subsequent sync instead of deleted
                self.errors -= len(failed)
                names = ', '.join(i.get('name', str(i['id'])) for i in failed)
                print('  Skipped {} in-use items from {} (will be overwritten): {}'.format(
                    len(failed), resource, names))

    def apply(self, config):
        if 'purge' in config:
            self.purge(config.pop('purge'))
        if config:
            self.__triage_and_apply(config)


def load_secrets(path):
    """Load secrets.yml and return as a dict, or empty dict if not found."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            print('ERROR: secrets file must be a flat key-value YAML mapping')
            sys.exit(1)
        return data
    except yaml.YAMLError as e:
        print('ERROR: Failed to parse {}: {}'.format(path, e))
        sys.exit(1)


def make_secret_loader(secrets):
    """Create a YAML loader class with a !secret tag constructor."""
    class SecretLoader(yaml.SafeLoader):
        pass

    def secret_constructor(loader, node):
        key = loader.construct_scalar(node)
        if key not in secrets:
            print('ERROR: secret "{}" not found in secrets.yml'.format(key))
            sys.exit(1)
        return secrets[key]

    SecretLoader.add_constructor('!secret', secret_constructor)
    return SecretLoader


def load_yaml(path, loader=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.load(f, Loader=loader) if loader else yaml.safe_load(f)
    except FileNotFoundError:
        print('ERROR: File not found: {}'.format(path))
        sys.exit(1)
    except yaml.YAMLError as e:
        print('ERROR: Failed to parse {}: {}'.format(path, e))
        sys.exit(1)


parser = argparse.ArgumentParser(description='Apply -arr app configuration from YAML')
parser.add_argument('config', nargs='?', default='./config/config.yml', help='Path to config YAML file')
parser.add_argument('--secrets', '-s', help='Path to secrets.yml (default: secrets.yml next to config)')
args = parser.parse_args()

config_dir = os.path.dirname(os.path.abspath(args.config))
secrets_path = args.secrets if args.secrets else os.path.join(config_dir, 'secrets.yml')
secrets = load_secrets(secrets_path)

if secrets:
    print('Loaded {} secret(s) from {}'.format(len(secrets), secrets_path))

loader = make_secret_loader(secrets)
configs = load_yaml(args.config, loader=loader)

total_errors = 0

for key in configs:
    config = configs[key]
    server = config.pop('server')

    if 'base_url' in server:
        base_url = server['base_url']
        if not base_url.startswith('http'):
            base_url = 'http://' + base_url
    else:
        address = server['address']
        if not address.startswith('http'):
            address = 'http://' + address
        base_url = '{}:{}'.format(address, server['port'])

    api_key = server.get('api_key') or server.get('apikey')
    api = Api(base_url, apikey=api_key, apiroot=server.get('apiroot'))

    print('\n=== {} ==='.format(key))
    api.initialize()

    if api.errors:
        print('ERROR: Failed to connect to {}. Skipping.'.format(key))
        total_errors += api.errors
        continue

    print('Applying configuration...')
    api.apply(config)
    total_errors += api.errors

if total_errors:
    print('\nFinished with {} error(s).'.format(total_errors))
    sys.exit(1)
else:
    print('\nFinished successfully.')
