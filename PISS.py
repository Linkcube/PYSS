# -*- coding: utf-8 -*-
# This program is provided as is, use it at your own risk.
# Python Icecast Stream Saver
# only for mp3 at the moment
import time, os, sys, re, json, imghdr
import requests
from requests.exceptions import ConnectionError
from mutagen.id3 import ID3, TIT2, TPE1, COMM, APIC
from mutagen.easyid3 import EasyID3
from pyquery import PyQuery
import threading

BLOCK_SIZE = 1024 # bytes
DJ_CHECK_INTERVAL = 60.0  # seconds
FILE_PATH = ""
INVALID_CHARACTERS = "<>:\"/\\|?*"
EXTENSION = "mp3"
TIMEOUT = 0
VALID_ARGS = ["-load", "-save", "-timeout", "-file_path", "-block_size", "-dj_check_interval", "-dj_url",
              "-dj_element", "-stream", "-stream_file", "-exclude_dj", "-np_element"]
CONFIG_FILE = "config.json"
DJ_URL = ""
DJ_ELEMENT = ""
DJ_IMG_ELEMENT = ""
NP_ELEMENT = None
CHECK_FOR_DJ = False
WHITE_SPACE = "\r                                                                     "
ICECAST_STATUS_LOCATION = "status-json.xsl"
EXCLUDE_DJ = []
DJ_ART = "dj_art"
CUE_ONLY = False
SONG_CHECK_INTERVAL = .5
DJ_DICT = {}
BITRATE_MOD = 1024 / 8
STREAM_DELAY = 2  # seems to be the normal value
CLI_LIMIT = 70


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
                    time.sleep(1)
                safe_stdout("\rError in updating stream, waiting..\n")
                time.sleep(SONG_CHECK_INTERVAL)

    def _update(self):
        xsl = requests.get(self.xsl_url)
        data = xsl.json()['icestats']['source'][1]
        self.bitrate = xsl.json()['icestats']['source'][0]["bitrate"]
        self.server_name = data["server_name"]
        self.server_type = data["server_type"]
        self.listener_peak = data['listener_peak']
        self.server_description = data['server_description']
        self.listeners = data['listeners']
        if data.get('title'):
            self.title = data['title']
        elif NP_ELEMENT and backup_get_title():
            self.title = backup_get_title()
        else:
            self.title = "Untitled %s" % int(time.time())


def get_stream_data():
    xsl = requests.get("http://stream.r-a-d.io/status-json.xsl")
    data = xsl.json()['icestats']['source'][1]
    # bitrate = xsl.json()['icestats']['source'][0]["bitrate"]
    stream_data = StreamData(data)
    return stream_data


def safe_stdout(to_print):
    """
    Print on any ascii terminal without crashing.
    """
    try:
        sys.stdout.write(to_print[:CLI_LIMIT])
    # TODO change to accurate exception
    except:
        sys.stdout.write(to_print.decode('utf-8')[:CLI_LIMIT])
    sys.stdout.flush()


def format_seconds(seconds):
    m, s = divmod(seconds, 60)
    return "%02d:%02d" % (m, s)


def record_stream(location, request, lock, panic):
    panic.acquire()
    with open(os.path.join(location, 'recording_bloc.%s' % EXTENSION), 'wb') as f:
        try:
            for block in request.iter_content():
                f.write(block)
                if lock.acquire(blocking=0):
                    f.close()
                    return
        except requests.exceptions.ChunkedEncodingError:
            # Stream issue, should go into an append recording
            panic.release()
            f.close()
            return


def swap_djs(location, name, dj_dict):
    dj_image_url = get_dj_art()
    response = requests.get(dj_image_url, stream=True)
    with open(os.path.join(location, name), "wb") as image:
        for chunk in response:
            image.write(chunk)
            dj_image_extension = imghdr.what(os.path.join(location, name))
    wait_on_file_rename(os.path.join(location, name),
                        os.path.join(location, "%s.%s" % (name, dj_image_extension)))
    dj_dict[name] = dj_image_extension


def begin_recording(stream_data, location, request, dj_dict):
    """
    Returns first whether to continue recording, and secondly if the recording is incomplete.
    """
    # TODO change over to tracking bits during recording rather than just time
    base_print = "\rRecording: %s" % stream_data.title
    writer_lock = threading.RLock()
    writer_lock.acquire()
    panic_lock = threading.RLock()
    writer = threading.Thread(target=record_stream, args=(location, request, writer_lock, panic_lock))
    writer.daemon = True
    writer.start()
    current_title = stream_data.title
    dj = ""
    song_start = time.time()
    start = True

    cue_file = open(os.path.join(location, "cue_file.txt"), 'w')

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
                safe_stdout("\r%s" % WHITE_SPACE)
                to_write = "DJ %s has taken over the stream.\n" % dj
                safe_stdout("\r%s" % to_write)
                cue_file.write(to_write)
                swap_djs(location, dj, dj_dict)
                dj_found = True

    try:
        while not panic_lock.acquire(blocking=0):
            stream_data.update()
            if stream_data.title != current_title:
                tag = "COMPLETE"
                if start:
                    tag = "INCOMPLETE"
                    start = False
                to_write = "%s %s %s\n" % (tag, current_title, int(time.time() - song_start))
                cue_file.write(to_write.encode("utf-8"))
                safe_stdout("\r%s" % WHITE_SPACE)
                safe_stdout("\rRecorded %s %s" % (current_title, format_seconds(time.time() - song_start)))
                safe_stdout("\n")
                song_start = time.time()
                base_print = "\rRecording: %s" % stream_data.title
                current_title = stream_data.title
                if CHECK_FOR_DJ:
                    new_dj = get_dj()
                    if new_dj != dj:
                        if new_dj in EXCLUDE_DJ:
                            safe_stdout("\rExcluded DJ detected, skipping %s" % new_dj)
                            raise KeyboardInterrupt
                        dj = new_dj
                        safe_stdout("\r%s" % WHITE_SPACE)
                        to_write = "DJ %s has taken over the stream.\n" % dj
                        safe_stdout("\rDJ %s has taken over the stream." % dj)
                        safe_stdout("\n")
                        cue_file.write(to_write)
                        swap_djs(location, dj)
            safe_stdout("\r%s %s" % (base_print, format_seconds(time.time() - song_start)))
            time.sleep(SONG_CHECK_INTERVAL)
    except KeyboardInterrupt:
        to_write = "INCOMPLETE %s %s\n" % (current_title, int(time.time() - song_start))
        cue_file.write(to_write.encode("utf-8"))
        writer_lock.release()
        cue_file.close()
        return False
    writer_lock.release()
    cue_file.close()
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
    return tag.text()


def get_dj_art():
    query = safe_query(DJ_URL)
    img_src = query(DJ_IMG_ELEMENT).attr('src')
    return DJ_URL + img_src


def backup_get_title():
    query = safe_query(DJ_URL)
    tag = query(NP_ELEMENT)
    return tag.text()


def make_file_name(title, folder_name, incomplete):
    name = title
    if not name:
        name = "DJ change detected"
    name = re.sub("[<>:\"/\\\|?*]", "", name)
    name = "%s" % os.path.join(FILE_PATH, folder_name, name)
    if incomplete:
        name += "_INCOMPLETE"
    name = "%s.%s" % (name, EXTENSION)
    return name


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


def wait_on_file_rename(file_name, new_name):
    done = False
    start = time.time()
    while not done:
        try:
            os.rename(file_name, new_name)
            done = True
        except WindowsError as e:
            time.sleep(.01)
            if 0 < TIMEOUT < time.time() - start:
                safe_stdout("\nTIMEOUT waiting for file access, quitting.")
                safe_stdout(e.message)
                quit()


def recording_loop(request, stream_data):
    do_continue = True
    while do_continue:
        dj_dict = {}
        folder_name = str(int(time.time()))
        location = os.path.join(FILE_PATH, folder_name)
        os.mkdir(location)
        try:
            do_continue = begin_recording(stream_data, location, request, dj_dict)
        finally:
            post_split(folder_name, dj_dict)


class Song:
    def __init__(self, raw_title, duration, dj, incomplete):
        self.raw_title = raw_title
        self.duration = duration
        self.dj = dj
        self.incomplete = incomplete

    def get_title(self):
        if len(self.raw_title.split('-')) == 2:
            return self.raw_title.split('-')[1]
        return self.raw_title

    def get_artist(self):
        if len(self.raw_title.split('-')) == 2:
            return self.raw_title.split('-')[0]
        if self.dj:
            return self.dj
        return ''


def tag_song(song, track_number, file_name, folder_name, dj_dict):
    audio = EasyID3()
    audio["title"] = song.get_title()
    audio["artist"] = song.get_artist()
    audio["albumartist"] = song.dj
    audio["album"] = os.path.dirname(file_name)
    audio["album"] = file_name
    audio["tracknumber"] = str(track_number)
    audio["date"] = str(int(time.time()))
    audio.save(file_name)
    tags = ID3(file_name)
    tags["COMM"] = COMM(encoding=3, lang=u'eng', desc='desc', text=u'Recorded with stream_saver')
    with open(os.path.join(folder_name, "%s.%s" % (song.dj, dj_dict[song.dj])), "rb") as dj_image:
        tags["APIC"] = APIC(encoding=3, mime='image/%s' % dj_dict[song.dj], type=3, desc="Cover",
                            data=dj_image.read())
    tags.save()


def post_split(folder, dj_dict):
    bloc_name = os.path.join(folder, 'recording_bloc.mp3')
    cue_name = os.path.join(folder, 'cue_file.txt')
    song_list = []
    dj = ''
    with open(cue_name, 'r') as cue_file:
        for line in cue_file.readlines():
            split_line = line.split(' ')
            if split_line[0] == "DJ":
                dj = ' '.join(split_line[1:-5])
            elif split_line[0] == "COMPLETE":
                song_list.append(Song(' '.join(split_line[1:-1]).decode('utf-8'), split_line[-1], dj.decode('utf-8'),
                                      False))
            elif split_line[0] == "INCOMPLETE":
                song_list.append(Song(' '.join(split_line[1:-1]).decode('utf-8'), split_line[-1], dj.decode('utf-8'),
                                      True))
    with open(bloc_name, 'rb') as b:
        bloc_file = b.read()
    last_start = 0
    index = 1
    for song in song_list:
        duration = int(float(song.duration)) * BITRATE_MOD
        if song == song_list[0]:
            duration -= int(round(STREAM_DELAY * BITRATE_MOD))
        if song == song_list[-1]:
            song_data = bloc_file[last_start:]
        else:
            song_data = bloc_file[last_start:last_start+duration]
            last_start += duration
        file_name = make_file_name("%s. %s" % (index, song.raw_title), folder, song.incomplete)
        with open(file_name, 'wb') as new_song:
            new_song.write(song_data)
        tag_song(song, index, file_name, folder, dj_dict)
        index += 1
    os.remove(bloc_name)


def setup():
    config_data = load_args()
    stream_url, stream_xsl = verify_config(config_data)
    optional_config(config_data)
    request = requests.get(stream_url, stream=True)
    a = time.time()
    stream_data = StreamData(stream_xsl)
    stream_data.update()
    d = time.time()
    """
    global STREAM_DELAY
    STREAM_DELAY = (d - a) * 2
    print STREAM_DELAY
    """
    global BITRATE_MOD
    BITRATE_MOD = int(BITRATE_MOD * stream_data.bitrate)
    return request, stream_data, stream_url


if __name__ == "__main__":
    setup_request, setup_data, setup_url = setup()
    recording_loop(setup_request, setup_data)
