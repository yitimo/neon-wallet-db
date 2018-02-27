from flask.ext.cache import Cache
from werkzeug.contrib.cache import MemcachedCache
from .server import application

# Constants
USE_MEMCACHE = False

## Cache
cache_config = {}
cache_config['CACHE_TYPE'] = 'simple'

### Memcache
