#! /usr/bin/python
import subprocess
import signal
import sys
from threading import Lock, Timer
from watchdog.observers import Observer
from watchdog.events import RegexMatchingEventHandler
 
class EventBurstHandler(RegexMatchingEventHandler):
    """
    Groups filesystem event bursts into one event to respond to.
    All filesystem events which occur within a configurable burst
    window, which should be set to a reasonably small length of
    time to allow for an editor to save a swap file, rename files,
    and do various other things.
    """
 
    def __init__(self, burst_window=0.1, *args, **kwargs):
        super(EventBurstHandler, self).__init__(*args, **kwargs)
        self.burst_window = burst_window
        self.burst_events = []
        self.burst_timer = None
        self.lock = Lock()
 
    def handle_event_burst(self, events):
        raise NotImplementedError('Please define in subclass')
 
    def on_any_event(self, event):
        with self.lock:
            self.burst_events.append(event)
            if self.burst_timer is not None:
                self.burst_timer.cancel()
            self.burst_timer = Timer(
                self.burst_window,
                self.on_burst
            )
            self.burst_timer.start()
 
    def on_burst(self):
        with self.lock:
            events = self.burst_events
            self.burst_events = []
            if len(events) == 0:
                return
       self.handle_event_burst(events)
 
class BuildDirectoryWatcher(EventBurstHandler):
    """
    A watchdog Filesystem Event Handler which watches a build
    directory for changes and, when an event occurs,
    restarts celery and gunicorn.
    """
 
    def __init__(self, build_path):
        handler_kwargs = {
            'ignore_directories': True,
            'ignore_regexes': [r'.*/.git/.*', r'.*/.idea/.*'],
        }
        super(BuildDirectoryWatcher, self).__init__(
            **handler_kwargs
        )
        self.build_path = build_path
        self.observer = None
 
    def watch(self):
        print(
            'Watching build dir for {0}.'.format(
                self.build_path
            )
        )
        self.observer = Observer()
        self.observer.schedule(
            event_handler=self,
            path=self.build_path,
            recursive=True,
        )
        self.observer.start()
 
    def handle_event_burst(self, events):
        for event in events:
            what = 'directory' if event.is_directory else 'file'
            print('{0} {1}: {2}'.format(
                event.event_type.capitalize(),
                what,
                event.src_path,
            ))
        print('Handling filesystem event burst.')
        proc = subprocess.Popen(
            [
                'docker', 'exec', 'compose_hub_1',
                'supervisorctl', 'restart', 'gunicorn'
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        with proc.stdout:
            for line in iter(proc.stdout.readline, b''):
                sys.stdout.write(line)
        if proc.returncode and proc.returncode != 0:
            raise Exception(
                "ERROR command exited with %r", proc.returncode
            )
 
        proc = subprocess.Popen(
            [
                'docker', 'exec', 'compose_hubworker_1',
                'supervisorctl', 'restart', 'celery'
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        with proc.stdout:
            for line in iter(proc.stdout.readline, b''):
                sys.stdout.write(line)
            if proc.returncode and proc.returncode != 0:
                raise Exception(
                    "ERROR command exited with %r", proc.returncode
                )
 
    def stop(self):
        if self.observer is not None:
            print('Stopping watch for {0}'.format(
                self.build_path,
            ))
            self.observer.stop()
 
    def join(self):
        if self.observer is not None:
            self.observer.join()
 
    def watch_until_interrupt(watchers):
        # Start watching.
        for watcher in watchers:
            watcher.watch()
 
        # Define interrupt signal handler.
        def interrupt_handler(signum, frame):
            print('Got Interrupt! Stopping watchers...')
            for watcher in watchers:
                watcher.stop()
                watcher.join()
 
            sys.exit(0)
 
        # Register interrupt signal handler.
        signal.signal(signal.SIGINT, interrupt_handler)
        while True:
            signal.pause()
 
def watch(paths):
    """
    Uses watchdog to monitor the build directories and
    automatically restart serivices.
    """
    watchers = [
        BuildDirectoryWatcher(path) for path in paths
    ]
    watch_until_interrupt(watchers)
 
if __name__ == "__main__":
    paths = sys.argv[1:] if len(sys.argv) &gt; 1 else '.'
    watch(paths)
