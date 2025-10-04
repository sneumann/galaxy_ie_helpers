#!/usr/bin/env python
from bioblend.galaxy import objects
from bioblend.galaxy import GalaxyInstance
from bioblend.galaxy.histories import HistoryClient
from bioblend.galaxy.datasets import DatasetClient
from bioblend.galaxy.dataset_collections import DatasetCollectionClient
import subprocess
import argparse
import re
import os
import zipfile
from string import Template
import logging
if os.environ.get('DEBUG', "False").lower() == 'true':
    logging.basicConfig(level=logging.DEBUG)
if os.environ.get('INFO', "False").lower() == 'true':
    logging.basicConfig(level=logging.INFO)
logging.getLogger("bioblend").setLevel(logging.CRITICAL)
log = logging.getLogger()


def _get_ip():
    """Get IP address for the docker host
    """
    cmd_netstat = ['netstat', '-nr']
    p1 = subprocess.Popen(cmd_netstat, stdout=subprocess.PIPE)
    cmd_grep = ['grep', '^0\.0\.0\.0']
    p2 = subprocess.Popen(cmd_grep, stdin=p1.stdout, stdout=subprocess.PIPE)
    cmd_awk = ['awk', '{ print $2 }']
    p3 = subprocess.Popen(cmd_awk, stdin=p2.stdout, stdout=subprocess.PIPE)
    galaxy_ip = p3.stdout.read().decode().strip()
    log.debug('Host IP determined to be %s', galaxy_ip)
    return galaxy_ip


def _test_url(url, key, history_id, obj=True):
    """Test the functionality of a given galaxy URL, to ensure we can connect
    on that address."""
    log.debug("TestURL url=%s obj=%s", url, obj)
    try:
        if obj:
            gi = objects.GalaxyInstance(url, key)
            gi.histories.get(history_id)
        else:
            gi = GalaxyInstance(url=url, key=key)
            gi.histories.show_history(history_id)
        log.debug("TestURL url=%s state=success", url)
        return gi
    except Exception:
        log.debug("TestURL url=%s state=failure", url)
        return None


def get_galaxy_connection(history_id=None, obj=True):
    """
        Given access to the configuration dict that galaxy passed us, we try and connect to galaxy's API.
        First we try connecting to galaxy directly, using an IP address given
        us by docker (since the galaxy host is the default gateway for docker).
        Using additional information collected by galaxy like the port it is
        running on and the application path, we build a galaxy URL and test our
        connection by attempting to get a history listing. This is done to
        avoid any nasty network configuration that a SysAdmin has placed
        between galaxy and us inside docker, like disabling API queries.
        If that fails, we failover to using the URL the user is accessing
        through. This will succeed where the previous connection fails under
        the conditions of REMOTE_USER and galaxy running under uWSGI.
    """
    history_id = history_id or os.environ['HISTORY_ID']
    key = os.environ['API_KEY']

    ### Customised/Raw galaxy_url ###
    galaxy_ip = _get_ip()
    # Substitute $DOCKER_HOST with real IP
    url = Template(os.environ['GALAXY_URL']).safe_substitute({'DOCKER_HOST': galaxy_ip})
    gi = _test_url(url, key, history_id, obj=obj)
    if gi is not None:
        return gi

    ### Failover, fully auto-detected URL ###
    # Remove trailing slashes
    app_path = os.environ['GALAXY_URL'].rstrip('/')
    # Remove protocol+host:port if included
    app_path = ''.join(app_path.split('/')[3:])

    if 'GALAXY_WEB_PORT' not in os.environ:
        # We've failed to detect a port in the config we were given by
        # galaxy, so we won't be able to construct a valid URL
        raise Exception("No port")
    else:
        # We should be able to find a port to connect to galaxy on via this
        # conf var: galaxy_paster_port
        galaxy_port = os.environ['GALAXY_WEB_PORT']

    built_galaxy_url = 'http://%s:%s/%s' % (galaxy_ip.strip(), galaxy_port, app_path.strip())
    url = built_galaxy_url.rstrip('/')

    gi = _test_url(url, key, history_id, obj=obj)
    if gi is not None:
        return gi

    ### Fail ###
    msg = "Could not connect to a galaxy instance. Please contact your SysAdmin for help with this error"
    raise Exception(msg)


def put(filenames, file_type='auto', history_id=None):
    """
        Given filename[s] of any file accessible to the docker instance, this
        function will upload that file[s] to galaxy using the current history.
        Does not return anything.
    """
    if type(filenames) is str:
        filenames = [filenames]

    history_id = history_id or os.environ['HISTORY_ID']
    gi = get_galaxy_connection(history_id=history_id)
    for filename in filenames:
        log.info('Uploading gx=%s history=%s localpath=%s ft=%s', gi, history_id, filename, file_type)
        history = gi.histories.get(history_id)
        history.upload_dataset(filename, file_type=file_type)


def find_matching_history_ids(list_of_regex_patterns,
                              identifier_type='hid', history_id=None):
    """
       This retrieves a list of matching ids for a list of
       user-specified regex(es). These can then be fed into
       the get function to retrieve them.

       Return value[s] are the history ids of the datasets.
    """
    # We only deal with arrays, even if only single regex given
    if type(list_of_regex_patterns) is str:
        list_of_regex_patterns = [list_of_regex_patterns]

    history_id = history_id or os.environ['HISTORY_ID']
    gi = get_galaxy_connection(history_id=history_id, obj=False)
    history_datasets = gi.histories.show_history(history_id=history_id)['state_ids']['ok']

    # Prepare regexes
    patterns = [re.compile(r, re.IGNORECASE) for r in list_of_regex_patterns]

    matching_ids = []
    for dataset in history_datasets:
        fstat = gi.datasets.show_dataset(dataset)
        fname = fstat["name"]
        fid = fstat["id"]
        fhid = fstat["hid"]

        for pat in patterns:
            if pat.match(fname):
                log.debug("Matched on history item %s (%s) : '%s' " % (fhid, fid, fname))
                matching_ids.append(fhid if identifier_type == "hid" else fid)

    # unique only
    return(list(set(matching_ids)))


def get(datasets_identifiers, identifier_type='hid', history_id=None, retrieve_datatype=None):
    """
        Given the history_id that is displayed to the user, this function will
        either search for matching files in the history if the identifier_type
        is set to 'regex', otherwise it will directly download the file[s] from
        the history and stores them under /import/.
        Return value[s] are the path[s] to the dataset[s] stored under /import/
    """
    history_id = history_id or os.environ['HISTORY_ID']
    # The object version of bioblend is to slow in retrieving all datasets from a history
    # fallback to the non-object path
    gi = get_galaxy_connection(history_id=history_id, obj=False)
    file_path_all = []
    datatypes_all = []

    if type(datasets_identifiers) is not list:
        datasets_identifiers = [datasets_identifiers]

    if identifier_type == "regex":
        datasets_identifiers = find_matching_history_ids(datasets_identifiers)
        identifier_type = "hid"

    hc = HistoryClient(gi)
    dc = DatasetClient(gi)
    dcc = DatasetCollectionClient(gi)

    for dataset_id in datasets_identifiers:
        file_path = '/import/%s' % dataset_id
        log.info('Downloading gx=%s history=%s dataset=%s', gi, history_id, dataset_id)
        # Cache the file requests. E.g. in the example of someone doing something
        # silly like a get() for a Galaxy file in a for-loop, wouldn't want to
        # re-download every time and add that overhead.
        if not os.path.exists(file_path):
            history = hc.show_history(history_id, contents=True)
            datasets = {ds[identifier_type]: {'id': ds['id'], 'type': ds['history_content_type']} for ds in history}
            if retrieve_datatype:
                datatypes_all.append({ds[identifier_type]: ds['extension'] for ds in history})
            if identifier_type == 'hid':
                dataset_id = int(dataset_id)

            if datasets[dataset_id]['type'] == 'dataset':
                dc.download_dataset(datasets[dataset_id]['id'], file_path=file_path, use_default_filename=False)
                file_path_all.append(file_path)
            else:
                log.info('Downloading collection gx=%s history=%s dataset=%s', gi, history_id, dataset_id)
                dcc.download_dataset_collection(datasets[dataset_id]['id'], file_path=file_path + '.zip')
                os.makedirs(file_path, exist_ok=True)
                with zipfile.ZipFile(file_path + '.zip', 'r') as zip_ref:
                    zip_ref.extractall(path=file_path)
                    file_path_all.extend([os.path.join(file_path, x) for x in zip_ref.namelist()])
        else:
            hc = HistoryClient(gi)
            dc = DatasetClient(gi)
            history = hc.show_history(history_id, contents=True)
            datatypes_all.append({ds[identifier_type]: ds['extension'] for ds in history})
            log.info('Cached, not re-downloading')
            if datasets[dataset_id]['type'] == 'dataset':
                file_path_all.append(file_path)
            else:
                # Walk instead of glob because glob will find folders.
                # Here we filter on things that are files, not directories.
                files = [[os.path.join(r, f1) for f1 in f] for (r, d, f) in os.walk(file_path) if len(f) > 0]
                # Flatten
                files = [item for sublist in files for item in sublist]
                file_path_all.extend(files)


    ## First path if only one item given, otherwise all paths.
    ## Should not break compatibility.
    if retrieve_datatype:
        if len(file_path_all) == 1:
            dataset_number = int(file_path_all[0].strip().split("/")[-1])
            return file_path_all, datatypes_all[0][dataset_number]
        else:
            datatype_multi = dict()
            for i in file_path_all:
                dataset_number = int(i.strip().split("/")[-1])
                datatype_multi[dataset_number] = datatypes_all[0][dataset_number]
            return file_path_all, datatype_multi
    else:
        return file_path_all[0] if len(file_path_all) == 1 else file_path_all

def get_user_history (history_id=None):
    """
       Get all visible dataset infos of user history.
       Return a list of dict of each dataset.
    """
    history_id = history_id or os.environ['HISTORY_ID']
    gi = get_galaxy_connection(history_id=history_id, obj=False)
    hc = HistoryClient(gi)
    history = hc.show_history(history_id, visible=True, contents=True)
    return history


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Connect to Galaxy through the API')
    parser.add_argument('--action',   help='Action to execute', choices=['get', 'put','get_user_history'])
    parser.add_argument('--history-id', dest="history_id", default=None,
                        help='History ID. The history ID and the dataset ID uniquly identify a dataset. Per default this is set to the current Galaxy history.')
    parser.add_argument('--argument', nargs='+', help='Files/ID numbers to Upload/Download.')
    parser.add_argument('-i', '--identifier_type', dest="identifier_type", choices=['hid', 'name'], default='hid',
                        help='Type of the identifiers hid for the dataset id within the history and name for dataset name. Per default, hid.')
    parser.add_argument('-t', '--filetype', default='auto',
                        help='Galaxy file format. If not specified Galaxy will try to guess the filetype automatically.')
    args = parser.parse_args()

    if args.action == 'get':
        get(args.argument, args.identifier_type, history_id=args.history_id)
    elif args.action == 'put':
        put(args.argument, file_type=args.filetype, history_id=args.history_id)
    elif args.action == 'get_user_history':
        get_user_history(history_id=args.history_id)
