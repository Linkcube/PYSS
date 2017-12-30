# This program is provided as is, use it at your own risk.
# Python Icecast Stream Saver
import time, os, sys, json, imghdr, re
import requests
from requests.exceptions import ConnectionError
from pyquery import PyQuery
import threading
import codecs
from multiprocessing import Process
from pydub import AudioSegment
from pydub.silence import split_on_silence
from mutagen.id3 import ID3, APIC
from mutagen.easyid3 import EasyID3

BLOCK_SIZE = 1024 # bytes
DJ_CHECK_INTERVAL = 5.0  # seconds
FILE_PATH = os.getcwd()
INVALID_CHARACTERS = "<>:\"/\\|?*"
EXTENSION = "mp3"
TIMEOUT = 0
VALID_ARGS = ["-load", "-save", "-timeout", "-file_path", "-block_size", "-dj_check_interval", "-dj_url",
              "-dj_element", "-stream", "-stream_file", "-exclude_dj", "-np_element", "-lazy"]
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
RESTART_ON_DJ_CHANGE = False

SERVER_TYPES = {"audio/mpeg": "mp3"}


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
                    safe_stdout("\nUpdate stream exceeded timeout limit, exiting program")
                    raise KeyboardInterrupt
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


class SongData():
    def __init__(self, index, location, title, ext, dj, dj_ext, bitrate, lead_up):
        self.index = index
        self.location = location
        self.raw_title = title
        self.extension = ext
        self.dj = dj
        self.dj_extension = dj_ext
        self.bitrate = bitrate
        self.lead_up = max(lead_up - 10, 0)

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

        self.raw_segment = os.path.join(self.location, "track_%.3d" % self.index)
        self.destination_file = os.path.join(self.location, "%s. %s.%s" % (
            self.index, re.sub('[' + INVALID_CHARACTERS + ']', '', self.raw_title), self.extension))
        self.dj_image = os.path.join(self.location, "%s.%s" % (self.dj, self.dj_extension))
        self.file_tags = {"artist": self.artist, "title": self.title, "albumartist": self.dj,
                          "album": os.path.dirname(self.location), "track": str(self.index),
                          "comments": "Recorded with PYSS"}


class NewDjException(Exception):
    pass


class ExcludedDjException(Exception):
    pass


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


def record_stream(location, request, lock, panic):
    panic.acquire()
    if CUE_ONLY:
        time.sleep(1)
        return
    try:
        for block in request.iter_content(chunk_size=1024):
            with open(os.path.join(location, "track_%.3d" % INDEX), 'ab') as f:
                f.write(block)
            with open(os.path.join(location, "track_%.3d" % (INDEX + 1)), 'ab') as f:
                f.write(block)
            if lock.acquire(blocking=0):
                return
    except requests.exceptions.ChunkedEncodingError:
        panic.release()


def wait_on_file_rename(file_name, new_name):
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


def process_raw_song(song, end=False):
    raw_song = AudioSegment.from_file(song.raw_segment, song.extension)
    if song.index > 1 and not end:
        raw_song = raw_song[int(song.lead_up * 1000):]
    chunks = split_on_silence(raw_song, min_silence_len=500, silence_thresh=-80)
    chunk_index = 1
    if song.index == 0 or len(chunks) == 1:
        chunk_index = 0
    chunks[chunk_index].export(song.destination_file, format=song.extension, bitrate="%sk" % song.bitrate)
    audio = EasyID3()
    audio["title"] = song.title
    audio["artist"] = song.artist
    audio["album"] = os.path.dirname(song.location)
    audio["albumartist"] = song.dj
    audio["tracknumber"] = str(song.index)
    audio["date"] = str(int(time.time()))
    audio.save(song.destination_file)
    tags = ID3(song.destination_file)
    with open(song.dj_image, "rb") as image:
        tags["APIC"] = APIC(encoding=3, mime='image/%s' % song.dj_extension, type=3, desc="Cover", data=image.read())
    tags.save()
    os.remove(song.raw_segment)


def begin_recording(stream_data, location, request):
    """
    Returns first whether to continue recording, and secondly if the recording is incomplete.
    """
    global INDEX
    INDEX = 0
    dj = ""
    dj_ext = ""
    cue_file = codecs.open(os.path.join(location, "cue_file.txt"), encoding='utf-8', mode='w')
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
                dj_ext = swap_djs(location, dj)
                safe_stdout(WHITE_SPACE)
                to_write = "DJ %s has taken over the stream. %s\n" % (dj, dj_ext)
                safe_stdout("\rDJ %s has taken over the stream." % dj)
                safe_stdout("\n")
                cue_file.write(to_write)
                dj_found = True

    writer_lock = threading.RLock()
    writer_lock.acquire()
    panic_lock = threading.RLock()
    writer = threading.Thread(target=record_stream, args=(location, request, writer_lock, panic_lock))
    writer.daemon = True
    writer.start()
    current_title = stream_data.title
    song_start = time.time()
    recording_start = song_start
    start = True
    bitrate = stream_data.bitrate
    audio_extension = SERVER_TYPES.get(stream_data.server_type, stream_data.server_type)
    song_processing = None
    safe_stdout("%.3d. %s" % (INDEX, stream_data.title))
    safe_stdout("\n")

    try:
        while not panic_lock.acquire(blocking=0):
            stream_data.update()
            if stream_data.title != current_title:
                if start:
                    start = False
                to_write = "%s %s\n" % (current_title, format_seconds(time.time() - song_start))
                cue_file.write(to_write)
                safe_stdout(WHITE_SPACE)
                if not CUE_ONLY:
                    current_song = SongData(INDEX, location, current_title, audio_extension, dj, dj_ext, bitrate,
                                            float(time.time() - song_start))
                    song_processing = Process(target=process_raw_song, args=(current_song,))
                    song_processing.start()
                INDEX += 1
                safe_stdout("\r%.3d. %s" % (INDEX, stream_data.title))
                safe_stdout('\n')
                song_start = time.time()
                current_title = stream_data.title
                bitrate = stream_data.bitrate
                audio_extension = SERVER_TYPES.get(stream_data.server_type, stream_data.server_type)
                if CHECK_FOR_DJ:
                    new_dj = get_dj()
                    if new_dj != dj:
                        if RESTART_ON_DJ_CHANGE:
                            raise NewDjException
                        if new_dj in EXCLUDE_DJ:
                            safe_stdout("\nExcluded DJ detected, skipping %s" % dj)
                            safe_stdout("\n")
                            raise ExcludedDjException
                        dj = new_dj
                        dj_ext = swap_djs(location, dj)
                        safe_stdout(WHITE_SPACE)
                        to_write = "DJ %s has taken over the stream. %s\n" % (dj, dj_ext)
                        safe_stdout("\rDJ %s has taken over the stream." % dj)
                        safe_stdout("\n")
                        cue_file.write(to_write)
            safe_stdout("\r%s: %s | %skbps | DJ: %s | Listeners: %.03d | %s / %s" % (
                stream_data.server_name, audio_extension.upper(), bitrate, dj,
                stream_data.listeners, format_seconds(time.time() - song_start),
                format_seconds(time.time() - recording_start)))
            time.sleep(SONG_CHECK_INTERVAL)
    except KeyboardInterrupt:
        safe_stdout("\nCleaning up and exiting program..")
        to_write = "INCOMPLETE %s %s\n" % (current_title, format_seconds(time.time() - song_start))
        cue_file.write(to_write)
        writer_lock.release()
        cue_file.close()
        writer.join()
        if not CUE_ONLY:
            if song_processing and song_processing.is_alive():
                song_processing.join()
            current_song = SongData(INDEX, location, current_title, audio_extension, dj, dj_ext, bitrate,
                                    float(time.time() - song_start))
            song_processing = Process(target=process_raw_song, args=(current_song, True))
            song_processing.start()
            song_processing.join()
            os.remove(os.path.join(location, "track_%.3d" % (INDEX + 1)))
        return False
    except NewDjException:
        safe_stdout("\nSetting up for next DJ..\n")
    except ExcludedDjException:
        safe_stdout("Starting new stream block..\n")
    writer_lock.release()
    writer.join()
    cue_file.close()
    if not CUE_ONLY:
        if song_processing and song_processing.is_alive():
            song_processing.join()
    return True


def safe_query(query):
    start = time.time()
    while True:
        try:
            return PyQuery(query)
        except ConnectionError as e:
            if 0 < TIMEOUT < time.time() - start:
                safe_stdout("PyQuery timed out with the following message: %s" % e.message)
                safe_stdout("\nQuitting the program now..")
                quit()
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
            elif arg == "-lazy_split":
                config_data["lazy_split"] = True
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


def recording_loop(request, stream_data):
    do_continue = True
    folder_name = str(int(time.time()))
    location = os.path.join(FILE_PATH, folder_name)
    os.mkdir(location)
    while do_continue:
        do_continue = begin_recording(stream_data, location, request)


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
    recording_loop(setup_request, setup_data)
