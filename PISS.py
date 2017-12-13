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
import logging

BLOCK_SIZE = 1024 # bytes
DJ_CHECK_INTERVAL = 60.0  # seconds
FILE_PATH = ""
INVALID_CHARACTERS = "<>:\"/\\|?*"
EXTENSION = "mp3"
TIMEOUT = 60.0
VALID_ARGS = ["-load", "-save", "-timeout", "-file_path", "-block_size", "-dj_check_interval", "-dj_url",
              "-dj_element", "-stream", "-stream_file", "-exclude_dj", "-np_element", "-cue_only"]
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
STREAM_LAG = 0.0
SONG_CHECK_INTERVAL = .5
FORMAT = "%s %(message)s" % time.time()
logging.basicConfig(format=FORMAT)
LOGGER = logging.getLogger()


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
            except Exception as e:
                if type(e) == KeyboardInterrupt:
                    raise KeyboardInterrupt
                if 0 < TIMEOUT < time.time() - start:
                    safe_stdout("\nUpdate stream exceeded timeout limit, exiting program")
                    time.sleep(1)
                safe_stdout("\rError in updating stream, waiting..\n")
                LOGGER.warning("Stream update error: %s" % e.message)
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
    LOGGER.info(to_print)
    try:
        sys.stdout.write(to_print[:79])
    # TODO change to accurate exception
    except:
        sys.stdout.write(to_print.encode('ascii', errors='ignore').decode()[:79])
    sys.stdout.flush()


def format_seconds(seconds):
    m, s = divmod(seconds, 60)
    return "%02d:%02d" % (m, s)


def clock_printer(base_print, lock):
    start_time = time.time()
    while not lock.acquire(blocking=0):
        safe_stdout("\r%s %s" % (base_print, format_seconds(time.time() - start_time)))
        time.sleep(1)


def record_stream(filename, write_method, request, lock, panic):
    panic.acquire()
    with open(filename, write_method) as f:
        try:
            for block in request.iter_content():
                f.write(block)
                if lock.acquire(blocking=0):
                    f.close()
                    return
        except requests.exceptions.ChunkedEncodingError:
            # Stream issue, should go into an append reccording
            panic.release()
            f.close()
            return


def begin_recording(stream_data, file_name, request, write_method, end_on_finish=False):
    """
    Returns first whether to continue recording, and secondly if the recording is incomplete.
    """
    #TODO: check whether you can retrieve metadata from the stream file to reduce desync
    last_check = time.time()
    base_print = "\rRecording: %s" % stream_data.title
    printer_lock = threading.RLock()
    printer_lock.acquire()
    writer_lock = threading.RLock()
    writer_lock.acquire()
    printer = threading.Thread(target=clock_printer, args=(base_print, printer_lock))
    printer.daemon = True
    printer.start()
    panic_lock = threading.RLock()
    writer = threading.Thread(target=record_stream, args=(file_name, write_method, request, writer_lock, panic_lock))
    writer.daemon = True
    writer.start()
    sleepy_end = False

    try:
        while not panic_lock.acquire(blocking=0):
            if not sleepy_end and time.time() - last_check > SONG_CHECK_INTERVAL:
                old_title = stream_data.title
                stream_data.update()
                if old_title != stream_data.title:
                    sleepy_end = True
                    last_check = time.time()
                else:
                    last_check = time.time()
            if sleepy_end and time.time() - last_check > STREAM_LAG:
                printer_lock.release()
                writer_lock.release()
                if end_on_finish:
                    return False, False
                else:
                    return True, False
    except KeyboardInterrupt:
        printer_lock.release()
        writer_lock.release()
        return False, True
    printer_lock.release()
    writer_lock.release()
    return True, True


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
    return config_data


def join_str_with_char(iterable, ch):
    i = 1
    whole = iterable[0]
    while i < len(iterable):
        whole += ch + iterable[i]
    return whole


def extract_xsl_from_link(link):
    split_link = link.split("/")
    split_link[-1] = ICECAST_STATUS_LOCATION
    return join_str_with_char(split_link, "/")


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
        CUE_ONLY = cue_only


def tag_file(title, artist, track_number, file_name, folder_name, dj_name, image_ext):
    audio = EasyID3()
    audio["title"] = title
    audio["artist"] = artist
    audio["album"] = folder_name.split(" ")[0]
    audio["albumartist"] = dj_name
    audio["tracknumber"] = str(track_number)
    audio["date"] = str(int(time.time()))
    audio.save(file_name)
    tags = ID3(file_name)
    tags["COMM"] = COMM(encoding=3, lang=u'eng', desc='desc', text=u'Recorded with stream_saver')
    with open(os.path.join(folder_name, "%s.%s" % (DJ_ART, image_ext)), "rb") as dj_image:
        tags["APIC"] = APIC(encoding=3, mime='image/%s' % image_ext, type=3, desc="Cover",
                            data=dj_image.read())
    tags.save()


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


def recording_loop(request, stream_data, stream_url):
    dj_name = ""
    first_run = True
    continue_recording = True
    folder_name = str(int(time.time()))
    track_number = 1
    dj_image_extension = ""
    if not CHECK_FOR_DJ:
        os.mkdir(os.path.join(FILE_PATH, folder_name))
    while continue_recording:
        if CHECK_FOR_DJ:
            new_dj_name = get_dj()
            if new_dj_name in EXCLUDE_DJ:
                safe_stdout("\rExcluded DJ detected, skipping %s" % new_dj_name)
                time.sleep(DJ_CHECK_INTERVAL)
                continue
            if dj_name != new_dj_name:
                track_number = 1
                dj_name = new_dj_name
                folder_name = "%s %s" % (int(time.time()), dj_name)
                os.mkdir(os.path.join(FILE_PATH, folder_name))
                safe_stdout("\nCurrent DJ: %s\n" % dj_name)
                dj_image_url = get_dj_art()
                response = requests.get(dj_image_url, stream=True)
                with open(os.path.join(folder_name, DJ_ART), "wb") as image:
                    for chunk in response:
                        image.write(chunk)
                        dj_image_extension = imghdr.what(os.path.join(folder_name, DJ_ART))
                wait_on_file_rename(os.path.join(folder_name, DJ_ART),
                                    os.path.join(folder_name, "%s.%s" % (DJ_ART, dj_image_extension)))
        file_name = make_file_name(stream_data.title, folder_name, False)
        rename_attempt = 1
        while os.path.isfile(file_name):
            file_name = make_file_name("%s_%d" % (stream_data.title, rename_attempt), folder_name, False)
            rename_attempt += 1
        old_title = stream_data.title
        continue_recording, incomplete = begin_recording(stream_data, file_name, request, "wb")
        try:
            artist, title = old_title.split(" - ")
        except ValueError:
            artist = title = ""
            safe_stdout("\nfailed to get artist - title from %s\n" % old_title)
        tag_file(title, artist, track_number, file_name, folder_name, dj_name, dj_image_extension)
        track_number += 1
        safe_stdout(WHITE_SPACE)
        if incomplete or not continue_recording or first_run:
            new_file_name = make_file_name(old_title, folder_name, True)
            wait_on_file_rename(file_name, new_file_name)
            safe_stdout("\rPartially Recorded: %s\n" % old_title)
            first_run = False
        else:
            safe_stdout("\rRecorded: %s\n" % old_title)
        request = requests.get(stream_url, stream=True)


def cue_interval(stream_data, cue):
    c_title = ""
    if CHECK_FOR_DJ:
        dj = ""
    while True:
        stream_data.update()
        if stream_data.title != c_title:
            c_title = stream_data.title
            if CHECK_FOR_DJ:
                new_dj = get_dj()
                if new_dj != dj:
                    dj = new_dj
                    to_write = "%s has taken over the stream.\n" % dj
                    safe_stdout(to_write)
                    cue.write(to_write)
            to_write = "%s (%s)\n" % (c_title, int(time.time()))
            safe_stdout(to_write)
            cue.write(to_write.encode("utf-8"))
        time.sleep(60)


def cue_only(stream_data):
    cue_file_name = "%s stream cue %s.txt" % (stream_data.server_name, int(time.time()))
    cue_file_name = re.sub("[<>:\"/\\\|?*]", "", cue_file_name)
    with open(cue_file_name, "w") as cue_file:
        try:
            cue_interval(stream_data, cue_file)
        except KeyboardInterrupt:
            print "Exiting Program"
    quit()


def setup():
    config_data = load_args()
    stream_url, stream_xsl = verify_config(config_data)
    optional_config(config_data)
    request = requests.get(stream_url, stream=True)
    a = time.time()
    stream_data = StreamData(stream_xsl)
    stream_data.update()
    d = time.time()
    # disabling stream lag till a proper test can be run to confirm the results actually help
    # global STREAM_LAG
    # STREAM_LAG = d - a
    return request, stream_data, stream_url


if __name__ == "__main__":
    setup_request, setup_data, setup_url = setup()
    if CUE_ONLY:
        cue_only(setup_data)
    else:
        recording_loop(setup_request, setup_data, setup_url)
