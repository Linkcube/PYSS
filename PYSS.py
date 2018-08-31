# This program is provided as is, use it at your own risk.
# Python Icecast Stream Saver
# ver 1.1.01
import time
import os
import sys
import json
import imghdr
import threading
import multiprocessing
import codecs
import requests
import pickle
from pyquery import PyQuery
from pydub import AudioSegment
from pydub.silence import detect_silence
from mutagen import MutagenError
from mutagen.id3 import ID3, APIC
from mutagen.easyid3 import EasyID3
from pydub.exceptions import TooManyMissingFrames

BLOCK_SIZE = 1024  # bytes
DJ_CHECK_INTERVAL = 5.0  # seconds
FILE_PATH = os.getcwd()
KEEP_CHARACTERS = (' ', '.', '_', '-')
EXTENSION = "mp3"
TIMEOUT = 0
VALID_ARGS = ["-load", "-save", "-timeout", "-file_path", "-block_size", "-dj_check_interval", "-dj_url",
              "-dj_element", "-stream", "-stream_file", "-exclude_dj", "-np_element", "-cue_only"]
CONFIG_FILE = "config.json"
DJ_URL = ""
DJ_ELEMENT = ""
DJ_IMG_ELEMENT = ""
NP_ELEMENT = None
CHECK_FOR_DJ = False
WHITE_SPACE = "\r%s" % "".join([" " for x in range(78)])
ICECAST_STATUS_LOCATION = "status-json.xsl"
EXCLUDE_DJ = []
CUE_ONLY = False
SONG_CHECK_INTERVAL = .5
CLI_LIMIT = 79
INDEX = 0
RESTART_ON_DJ_CHANGE = True
PREVIOUS_SNIP = None
SILENCE_CHECK = 100
STREAM_DELAY = 0
SERVER_TYPES = {"audio/mpeg": "mp3"}
MAX_DURATION = 900  # 15 minutes
MAX_TITLE = 200  # max title length, go even less for android
QUICK_PROC = False
# Whether to completely calculate the silence for each song or just the first time  (~1 second per song speedup)


class StreamData:
    """
    Structure to hold streaming data
    """
    def __init__(self, url):
        self.xsl_url = url
        self.bitrate = None
        self.server_name = None
        self.server_type = None
        self.listener_peak = None
        self.server_description = None
        self.listeners = None
        self.title = None

    def update(self):
        done = False
        start = time.time()
        while not done:
            try:
                self._update()
                done = True
            # TODO change to accurate exception
            # TODO change the title to represent that an error has occured (which will be fixed when updated)
            except Exception as e:
                if type(e) == KeyboardInterrupt:
                    raise KeyboardInterrupt
                if 0 < TIMEOUT < time.time() - start:
                    raise RequestException
                safe_stdout("\rError in updating stream, probably a dj change..")
                time.sleep(SONG_CHECK_INTERVAL)

    def _update(self):
        xsl = requests.get(self.xsl_url)
        data = xsl.json()['icestats']['source'][1]
        self.bitrate = xsl.json()['icestats']['source'][0]["bitrate"]
        self.server_name = unicode(data["server_name"])
        self.server_type = data["server_type"]
        self.listener_peak = data['listener_peak']
        self.server_description = unicode(data['server_description'])
        self.listeners = data['listeners']
        if data.get('title'):
            self.title = unicode(data['title'])
        elif NP_ELEMENT and backup_get_title():
            self.title = backup_get_title()
        else:
            self.title = "Untitled %s" % int(time.time())


class SongData:
    def __init__(self, index, location, title, ext, dj, dj_ext, bitrate, duration, album, part=0, last_part=False):
        self.index = index
        self.location = location
        self.raw_title = title
        self.extension = ext
        self.dj = dj
        self.dj_extension = dj_ext
        self.bitrate = bitrate
        self.duration = duration
        self.album = album
        self.part = part
        self.last_part = last_part

        # Check if author - title splits cleanly
        if len(self.raw_title.split('-')) == 2:
            self.title = self.raw_title.split('-')[1]
        else:
            self.title = self.raw_title

        if len(self.raw_title.split('-')) == 2:
            self.artist = self.raw_title.split('-')[0]
        elif self.dj:
            self.artist = self.dj
        else:
            self.artist = ''

        #  Ensure that parts are unique to an index
        if part == 0:
            file_index = self.index
        else:
            file_index = "{}.{}".format(self.index, self.part)

        cleaned_title = "".join(c for c in self.raw_title if c.isalnum() or c in KEEP_CHARACTERS).rstrip()

        self.raw_segment = os.path.join(self.location, "track_%s" % self.index)
        self.destination_file = os.path.join(self.location, "%s. %s.%s" % (
            file_index, cleaned_title[:MAX_TITLE], self.extension))
        self.dj_image = os.path.join(self.location, "%s.%s" % (self.dj, self.dj_extension))
        self.file_tags = {"artist": self.artist, "title": self.title, "albumartist": self.dj,
                          "album": os.path.dirname(self.location), "track": file_index,
                          "comments": "Recorded with PYSS"}
        self.index = file_index

    def split(self):
        duration = self.duration
        parts = []
        part_index = 0
        while duration > MAX_DURATION:
            parts.append(
                SongData(self.index, self.location, self.raw_title, self.extension, self.dj, self.dj_extension,
                         self.bitrate, MAX_DURATION, self.album, part=part_index))
            duration -= MAX_DURATION
            part_index += 1
        parts.append(
            SongData(self.index, self.location, self.raw_title, self.extension, self.dj, self.dj_extension,
                     self.bitrate,  duration, self.album, part=part_index, last_part=True))
        return parts


class EasyWrite:
    def __init__(self, file_location):
        self.file_location = file_location

    def write(self, content):
        with codecs.open(self.file_location, encoding='utf-8', mode='a') as f:
            f.write(content)


class NewDjException(Exception):
    pass


class ExcludedDjException(Exception):
    pass


class RequestException(Exception):
    pass


class StreamRecorder():
    def __init__(self, location):
        self.location = location
        self.panic_lock = threading.RLock()

    def _record_stream(self, request, writer_lock):
        self.panic_lock.acquire()
        cache_start = time.time()
        index = 0
        if CUE_ONLY:
            time.sleep(1)
            return
        try:
            for block in request.iter_content(chunk_size=1024):
                if block is None:
                    print "Recieved 0 bytes from stream."
                    self.panic_lock.release()
                if time.time() - MAX_DURATION > cache_start:
                    cache_start = time.time()
                    index += 1
                    with open(os.path.join(self.location, "track_%s" % index), 'wb') as f:
                        f.write(block)
                else:
                    with open(os.path.join(self.location, "track_%s" % index), 'ab') as f:
                        f.write(block)
                if writer_lock.acquire(blocking=0):
                    return
        except requests.exceptions.ChunkedEncodingError as e: # or httplib.IncompleteRead as e:
            print e.message
            self.panic_lock.release()

    def record_stream(self, request, writer_lock):
        writer = threading.Thread(target=self._record_stream, args=(request, writer_lock))
        writer.daemon = True
        writer.start()
        return self.panic_lock, writer


def safe_stdout(to_print):
    """
    Print on any ascii terminal without crashing.
    """
    try:
        sys.stdout.write(to_print[:CLI_LIMIT])
    # TODO change to accurate exception
    except:
        sys.stdout.write(to_print.encode('ascii', errors='ignore')[:CLI_LIMIT])
    sys.stdout.flush()


def format_seconds(seconds):
    m, s = divmod(seconds, 60)
    return "%02d:%02d" % (m, s)


def format_with_hours(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return "%02d:%02d:%02d" % (h, m, s)


def wait_on_file_rename(file_name, new_name):
    if os.path.isfile(new_name):
        os.remove(new_name)
    done = False
    start = time.time()
    while not done:
        try:
            os.rename(file_name, new_name)
            done = True
        except WindowsError or OSError as e:
            time.sleep(.01)
            if 0 < TIMEOUT < time.time() - start:
                safe_stdout("\nTIMEOUT waiting for file access, quitting.")
                safe_stdout(e.message)
                raise e


def swap_djs(location, name):
    dj_image_url = get_dj_art()
    response = requests.get(dj_image_url, stream=True)
    file_name = os.path.join(location, name)
    with open(file_name, "wb") as image:
        for chunk in response:
            image.write(chunk)
    dj_image_extension = imghdr.what(file_name)
    wait_on_file_rename(file_name, "%s.%s" % (file_name, dj_image_extension))
    return dj_image_extension


class SongProcessor:
    def __init__(self, cue_path):
        self.cue_path = cue_path
        self.unpacker = None
        self.file_index = 0
        self.file_progress = 0
        self.left_over = None
        self.raw = None
        self.delay = None

    def _new_proc(self, song, complete):
        sample_duration = 10 * 1000
        remaining_duration = song.duration

        # Stick files if at the end of a file
        if (remaining_duration > self.raw.duration_seconds - self.file_progress and
                os.path.isfile(os.path.join(song.location, "track_{}".format(self.file_index + 1)))):
            part_a = self.raw[self.file_progress * 1000:]
            remaining_duration -= part_a.duration_seconds
            self.file_index += 1
            del self.raw
            self.raw = AudioSegment.from_file(os.path.join(song.location, "track_{}".format(self.file_index)),
                                              song.extension)
            if remaining_duration > self.raw.duration_seconds:
                part_b = self.raw
            else:
                part_b = self.raw[:remaining_duration * 1000]
            raw = part_a + part_b
            self.file_progress = 0
        else:
            if complete:
                raw = self.raw[self.file_progress * 1000:(self.file_progress + remaining_duration) * 1000]
            else:
                raw = self.raw[self.file_progress * 1000:]

        self.file_progress += remaining_duration

        if self.left_over and song.part == 0:
            raw = self.left_over + raw
            self.left_over = None

        if song.last_part and complete:
            if not self.delay or not QUICK_PROC:
                if raw.duration_seconds > 10:
                    sample_range = raw[-1 * sample_duration:]
                else:
                    sample_range = raw
                try:
                    ranges = detect_silence(sample_range, min_silence_len=SILENCE_CHECK, silence_thresh=-80)
                except TooManyMissingFrames:
                    ranges = None
                if ranges:
                    self.delay = ranges[-1][1] - sample_duration
                else:
                    self.delay = 0
            if self.delay == 0:
                post = raw
            else:
                post = raw[:self.delay]
                try:
                    self.left_over = raw[self.delay:]
                except TooManyMissingFrames:
                    post = raw
                    self.left_over = None
        else:
            post = raw

        post.export(song.destination_file, format=song.extension, bitrate="%sk" % song.bitrate)
        audio = EasyID3()
        audio["title"] = song.title
        audio["artist"] = song.artist
        audio["album"] = song.album
        audio["albumartist"] = song.dj
        audio["tracknumber"] = str(song.index)
        audio["date"] = str(int(time.time()))
        audio.save(song.destination_file)
        if song.dj_image:
            tags = ID3(song.destination_file)
            with open(song.dj_image, "rb") as image:
                tags["APIC"] = APIC(encoding=3, mime='image/%s' % song.dj_extension, type=3, desc="Cover",
                                    data=image.read())
            try:
                tags.save()
            except MutagenError:
                time.sleep(1.0)
                tags.save()

    def unpack_cue(self):
        self.delay = None
        cue_file = open(self.cue_path, 'r')
        dj_pack = pickle.load(cue_file)
        if dj_pack[0]:
            dj = dj_pack[1]
            dj_ext = dj_pack[2]

        songs = []
        while True:
            try:
                songs.append(pickle.load(cue_file))
            except EOFError:
                break
        cue_file.close()
        self.raw = AudioSegment.from_file(
            os.path.join(songs[0][0].location, "track_0"), songs[0][0].extension)
        for song in songs:
            for part in song[0].split():
                self._new_proc(part, song[1])
        for track in range(0, self.file_index+1):
            os.remove(os.path.join(songs[0][0].location, "track_{}".format(track)))

    def threaded_unpack(self):
        self.unpacker = threading.Thread(target=self.unpack_cue)
        self.unpacker.start()

    def mp_unpack(self):
        self.unpacker = multiprocessing.Process(target=self.unpack_cue)
        self.unpacker.start()

    def join(self):
        if self.unpacker:
            self.unpacker.join()


def begin_recording(stream_data, stream_url):
    """
    Returns first whether to continue recording, and secondly if the recording is incomplete.
    """
    song_index = 1
    dj = ""
    dj_ext = ""

    try:
        if CHECK_FOR_DJ:
            dj_found = False
            while not dj_found:
                new_dj = get_dj()
                if new_dj != dj:
                    if new_dj in EXCLUDE_DJ:
                        safe_stdout("\rExcluded DJ detected, skipping %s" % new_dj)
                        time.sleep(DJ_CHECK_INTERVAL)
                        continue
                    dj = new_dj
                    dj_found = True
    except KeyboardInterrupt:
        safe_stdout("\nQuitting program..")
        return False, None

    folder_name = album = str(int(time.time()))
    if dj:
        folder_name += " %s" % dj
    location = os.path.join(FILE_PATH, folder_name)
    os.mkdir(location)
    cue_path = os.path.join(location, "cue_file.txt")
    cue_file = open(cue_path, 'w+')

    if CHECK_FOR_DJ:
        dj_ext = swap_djs(location, dj)
        safe_stdout(WHITE_SPACE)
        to_write = [True, dj, dj_ext]
        safe_stdout("\rDJ %s has taken over the stream." % dj)
        safe_stdout("\n")
        pickle.dump(to_write, cue_file)
    else:
        to_write = [False]
        pickle.dump(to_write, cue_file)

    writer_lock = threading.RLock()
    writer_lock.acquire()
    writer = StreamRecorder(location)
    request = requests.get(stream_url, stream=True)
    panic_lock, writer_thread = writer.record_stream(request, writer_lock)
    current_title = stream_data.title
    song_start = time.time()
    recording_start = song_start
    bitrate = stream_data.bitrate
    audio_extension = SERVER_TYPES.get(stream_data.server_type,
                                       stream_data.server_type)
    safe_stdout("%.3d. %s %s" % (song_index, stream_data.title,
                                 format_with_hours(time.time() - recording_start)))
    safe_stdout("\n")

    try:
        while not panic_lock.acquire(blocking=0):
            stream_data.update()
            if stream_data.title != current_title:
                current_song = SongData(
                    song_index, location, current_title, audio_extension, dj,
                    dj_ext, bitrate,  float(time.time() - song_start), album)
                to_write = [current_song, True]
                pickle.dump(to_write, cue_file)
                safe_stdout(WHITE_SPACE)
                song_index += 1
                safe_stdout("\r%.3d. %s %s" % (song_index, stream_data.title,
                                               format_with_hours(time.time() - recording_start)))
                safe_stdout('\n')
                song_start = time.time()
                current_title = stream_data.title
                bitrate = stream_data.bitrate
                audio_extension = SERVER_TYPES.get(
                    stream_data.server_type, stream_data.server_type)
                if CHECK_FOR_DJ:
                    new_dj = get_dj()
                    if new_dj != dj:
                        if new_dj in EXCLUDE_DJ:
                            safe_stdout("\nExcluded DJ detected, skipping %s" % dj)
                            safe_stdout("\n")
                            raise ExcludedDjException
                        raise NewDjException
            safe_stdout("\r%s: %s %skbps | DJ: %s | Listeners: %.04d | %s / %s" % (
                stream_data.server_name, audio_extension.upper(), bitrate, dj,
                stream_data.listeners, format_seconds(time.time() - song_start),
                format_with_hours(time.time() - recording_start)))
            time.sleep(SONG_CHECK_INTERVAL)
    except KeyboardInterrupt:
        safe_stdout("\nCleaning up and exiting program..")
        current_song = SongData(
            song_index, location, current_title, audio_extension, dj, dj_ext,
            bitrate, float(time.time() - song_start), album)
        to_write = [current_song, False]
        pickle.dump(to_write, cue_file)
        writer_lock.release()
        writer_thread.join()
        return False, cue_path
    except NewDjException:
        safe_stdout("\nSetting up for next DJ..\n")
    except ExcludedDjException:
        safe_stdout("Starting new stream block..\n")
    except RequestException:
        safe_stdout("\nError in the query, restarting recording..\n")
    writer_lock.release()
    writer_thread.join()
    return True, cue_path


def safe_query(query):
    start = time.time()
    printed = False
    while True:
        try:
            return PyQuery(query)
        except:
            if not printed:#0 < TIMEOUT < time.time() - start:
                print ("\nPyQuery timed out")  # with the following message: %s" % e.message)
                printed = True
                #raise e
            time.sleep(1.0)


def get_dj():
    query = safe_query(DJ_URL)
    tag = query(DJ_ELEMENT)
    return unicode(tag.text())


def get_dj_art():
    query = safe_query(DJ_URL)
    img_src = query(DJ_IMG_ELEMENT).attr('src')
    return DJ_URL + img_src


def backup_get_title():
    query = safe_query(DJ_URL)
    tag = query(NP_ELEMENT)
    return unicode(tag.text())


def load_args():
    """
    Load command line arguments
    """
    i = 1
    config_data = {}
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in VALID_ARGS:
            if arg == "-load":
                config_data = load_config(sys.argv[i + 1])
                i += 1
            elif arg == "-save":
                config_data['save_flag'] = sys.argv[i + 1]
                i += 1
            elif arg == "-timeout":
                config_data['timeout'] = float(sys.argv[i + 1])
                i += 1
            elif arg == "-file_path":
                config_data['file_path'] = sys.argv[i + 1]
                i += 1
            elif arg == "-block_size":
                config_data['block_size'] = int(sys.argv[i + 1])
                i += 1
            elif arg == "-dj_check_interval":
                config_data['dj_check_interval'] = int(sys.argv[i + 1])
                i += 1
            elif arg == "-dj_url":
                config_data['dj_url'] = sys.argv[i + 1]
                i += 1
            elif arg == "-dj_element":
                config_data['dj_element'] = sys.argv[i + 1]
                i += 1
            elif arg == "-stream":
                config_data['stream_url'] = sys.argv[i + 1]
                config_data['xsl_location'] = extract_xsl_from_link(sys.argv[i + 1])
            elif arg == "-stream_file":
                with open(sys.argv[i + 1]) as f:
                    link = f.readline()
                    config_data['stream_url'] = link
                    config_data['xsl_location'] = extract_xsl_from_link(link)
            elif arg == "-dj_img_element":
                config_data['dj_img_element'] = sys.argv[i + 1]
                config_data['dj_img_element'] = extract_xsl_from_link(sys.argv[i + 1])
                i += 1
            elif arg == "-exclude":
                if config_data.get('-exclude_dj'):
                    config_data['exclude_dj'].append(sys.argv[i + 1])
                else:
                    config_data['exclude_dj'] = [sys.argv[i + 1]]
                i += 1
            elif arg == "-np_element":
                config_data['np_element'] = sys.argv[i + 1]
                i += 1
            elif arg == "-cue_only":
                config_data['cue_only'] = True
                i += 1

        i += 1
    return config_data


def extract_xsl_from_link(link):
    split_link = link.split("/")
    split_link[-1] = ICECAST_STATUS_LOCATION
    return '/'.join(split_link)


def save_config(config):
    new_config_name = config['save_flag']
    config.pop('save_flag', None)
    with open(CONFIG_FILE, 'r') as config_file:
        data = json.load(config_file)
    data['configs'][new_config_name] = config
    with open(CONFIG_FILE, 'w') as config_file:
        config_file.write(json.dumps(data))


def load_config(config_name):
    with open(CONFIG_FILE) as config:
        data = json.load(config)
        if data.get('stream_url'):
            data['xsl_location'] = extract_xsl_from_link(data['stream_url'])
    return data['configs'][config_name]


def verify_config(config):
    try:
        return config["stream_url"], config["xsl_location"]
    except KeyError as e:
        print e.message
        print "Invalid configuration, exiting program"
        quit()


def optional_config(config):
    dj_url = config.get("dj_url")
    dj_element = config.get("dj_element")
    save_flag = config.get("save_flag")
    timeout = config.get("timeout")
    file_path = config.get("file_path")
    block_size = config.get("block_size")
    dj_check_interval = config.get("dj_check_interval")
    dj_img_element = config.get("dj_img_element")
    exclude_dj = config.get("exclude_dj")
    np_element = config.get("np_element")
    cue = config.get("cue_only")

    if save_flag:
        save_config(config)
    if timeout:
        global TIMEOUT
        TIMEOUT = timeout
    if dj_url and dj_element:
        global DJ_ELEMENT, DJ_URL, CHECK_FOR_DJ
        DJ_URL = dj_url
        DJ_ELEMENT = dj_element
        if get_dj() != "":
            CHECK_FOR_DJ = True
    if file_path:
        global FILE_PATH
        FILE_PATH = file_path
    if block_size:
        global BLOCK_SIZE
        BLOCK_SIZE = block_size
    if dj_check_interval:
        global DJ_CHECK_INTERVAL
        DJ_CHECK_INTERVAL = DJ_CHECK_INTERVAL
    if dj_img_element:
        global DJ_IMG_ELEMENT
        DJ_IMG_ELEMENT = dj_img_element
    if exclude_dj:
        global EXCLUDE_DJ
        EXCLUDE_DJ = exclude_dj
    if np_element:
        global NP_ELEMENT
        NP_ELEMENT = np_element
    if cue:
        global CUE_ONLY
        CUE_ONLY = True


def recording_loop(stream_url, stream_data):
    do_continue = True
    proc = None
    while do_continue:
        do_continue, cue_file = begin_recording(stream_data, stream_url)
        if cue_file:
            proc = SongProcessor(cue_file)
            proc.mp_unpack()
    if proc:
        proc.join()


def get_avg_delay(stream_xsl):
    summ = 0
    samples = 4
    for x in range(samples):
        a = time.time()
        stream_data = StreamData(stream_xsl)
        stream_data.update()
        d = time.time()
        summ += d - a
    return summ / float(samples)


def setup():
    config_data = load_args()
    stream_url, stream_xsl = verify_config(config_data)
    optional_config(config_data)
    request = requests.get(stream_url, stream=True)
    stream_data = StreamData(stream_xsl)
    stream_data.update()
    safe_stdout("Connected to %s\n" % stream_data.server_name)
    safe_stdout("%s\n" % stream_data.server_description)
    return request, stream_data, stream_url


if __name__ == "__main__":
    setup_request, setup_data, setup_url = setup()
    recording_loop(setup_url, setup_data)
