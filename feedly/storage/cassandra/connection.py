import copy
from pycassa.pool import ConnectionPool
from pycassa.system_manager import SystemManager
import logging
import time

logger = logging.getLogger(__name__)


connection_pool_cache = dict()
CONNECTION_POOL_MAX_AGE = 5*60


def detect_nodes(seeds, keyspace):
    from feedly import settings
    if not settings.FEEDLY_DISCOVER_CASSANDRA_NODES:
        logger.warning('cassandra nodes discovery is off')
        return seeds
    nodes = frozenset(seeds)
    logging.info('retrieve nodes from seeds %r' % seeds)
    for seed in seeds:
        sys_manager = SystemManager(seed)
        ring_description = sys_manager.describe_ring(keyspace)
        for ring_range in ring_description:
            endpoint_details = ring_range.endpoint_details[0]
            hostname = endpoint_details.host
            port = getattr(endpoint_details, 'port', 9160)
            nodes = nodes.union({'%s:%s' % (hostname, port),}, nodes)
    return nodes

class FeedlyPoolListener(object):

    def __init__(self, connection_pool=None):
        self.connection_pool = connection_pool
        self.host_error_count = {}

    def eject_host(self, host):
        logging.warning('ejecting %s from pool' % host)
        host_list = copy.copy(self.connection_pool.server_list)
        host_list.remove(host)
        self.connection_pool.set_server_list(host_list)

    def connection_failed(self, dic):
        self.eject_host(dic['server'])

def connection_pool_expired(created_at):
    return created_at + CONNECTION_POOL_MAX_AGE < time.time()

def get_cassandra_connection(keyspace_name, hosts):
    key = keyspace_name, tuple(hosts)
    connection_pool, created_at = connection_pool_cache.get(key, (None, None))

    init_new_pool = connection_pool is None or connection_pool_expired(created_at)

    if not init_new_pool and len(connection_pool.server_list) == 0:
        logging.warning('connection pool had no active hosts')
        init_new_pool = True

    if init_new_pool:
        nodes = detect_nodes(hosts, keyspace_name)
        logger.info('setting up a new connection pool')
        pool_size = len(nodes) * 4
        connection_pool = ConnectionPool(
            keyspace_name,
            nodes,
            pool_size=pool_size,
            prefill=True,
            timeout=10,
            max_retries=5
        )
        listener = FeedlyPoolListener(connection_pool)
        connection_pool.add_listener(listener)
        connection_pool_cache[key] = (connection_pool, time.time())
    return connection_pool

