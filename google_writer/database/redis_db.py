import redis

from config_data.bot_conf import conf

r = redis.StrictRedis(host=conf.redis_db.redis_host,
                      port=conf.redis_db.REDIS_PORT,
                      password=conf.redis_db.REDIS_PASSWORD,
                      db=conf.redis_db.redis_db_num)




