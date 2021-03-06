#!/usr/bin/env python3
"""
Download parkeerrechten database dumps from the objectstore and restore them.
"""
# TODO: mark some traffic as intra data center ...

import sys
import logging
from tempfile import TemporaryDirectory
import os
import subprocess

from sqlalchemy import create_engine
from sqlalchemy.sql import text

from . import settings
from . import objectstore
from . import backup
from . import namecheck
from . import commandline

DP_ENGINE = create_engine(settings.DATAPUNT_DB_URL)

LOG_FORMAT = '%(asctime)-15s - %(name)s - %(message)s'
logging.basicConfig(format=LOG_FORMAT, level=logging.DEBUG)
logger = logging.getLogger('dump_database')


def _pg_restore(file_name):
    """
    Construct pg_restore command line and execute it.
    """
    # Note: For now the target database not parametrized, following must match what
    # is present in dump_database.py script.
    cmd = [
        'pg_restore',
        '--host=database',
        '--username=parkeerrechten',
        '--port=5432',
        '--no-password',  # use .pgpass (or fail)
        '--format=c',
        '--table={}'.format(settings.TARGET_TABLE),
        '--dbname=parkeerrechten',
        file_name
    ]
    logger.info('Running command: %s', cmd)
    p = subprocess.Popen(cmd)
    p.wait()
    logger.info('Return code: %d', p.returncode)


def _restore_database(raw_args, dp_conn):
    """
    Restore the individual pg_dump files from the object store.
    """
    args = commandline.parse_args(raw_args, include_orphans_option=False)

    # Check that we are working from an empty table
    table_content = backup.get_batch_names_in_database(
        dp_conn, settings.TARGET_TABLE, include_leeg=True, require_table=False)
    if table_content:
        logging.error('Table we are restoring to is not empty, exiting.')
        return

    # Check the object store for backups
    batch_names = backup.get_batch_names_in_objectstore(include_leeg=True)
    batch_names = namecheck.filter_batch_names_by_date(
        batch_names, args.startdate, args.enddate)

    # loop: download file, restore etc...
    logging.info('Starting restore')
    logging.info(
        '\n\nERRORS MESSAGES ABOUT PRE-EXISTING TABLE EXPECTED BELOW - HARMLESS\n\n')
    with TemporaryDirectory() as temp_dir:
        for i, batch_name in enumerate(batch_names):
            file_name = namecheck.file_name_for_batch_name(batch_name)
            dump_file = objectstore.copy_file_from_objectstore(
                settings.OBJECT_STORE_CONTAINER, file_name, temp_dir)

            _pg_restore(os.path.join(temp_dir, dump_file))

            os.remove(dump_file)

    _erase_fields(dp_conn, settings.TARGET_TABLE, settings.SENSITIVE_FIELDS)

    table_content = backup.get_batch_names_in_database(
        dp_conn, settings.TARGET_TABLE, include_leeg=True, require_table=False)

    logging.debug('Present after restore: %s', str(table_content))


def _erase_fields(dp_conn, table_name, fields):
    for field_name in fields:
        # update <TABLE_NAME> set <MY_COLUMN> = null
        sql = '''update "{}" set "{}" = null;'''.format(table_name, field_name)
        dp_conn.execute(text(sql))


def main():
    with DP_ENGINE.connect() as dp_conn:
        _restore_database(sys.argv[1:], dp_conn)


if __name__ == '__main__':
    main()
