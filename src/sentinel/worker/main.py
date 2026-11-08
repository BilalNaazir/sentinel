import os

from redis import Redis
from rq import Queue, SimpleWorker, Worker

from sentinel.settings import get_settings


def main() -> None:
    conn = Redis.from_url(get_settings().redis_url)
    queue = Queue("default", connection=conn)
    # Normal RQ workers use fork(), which Windows doesn't have.
    # SimpleWorker runs each job in the same process, so it works on Windows.
    worker_class = SimpleWorker if os.name == "nt" else Worker
    worker_class([queue], connection=conn).work()


if __name__ == "__main__":
    main()