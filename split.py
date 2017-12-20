# -*- coding: utf-8 -*-
"""
Used to split a single mp3 blcok into individual tracks.

Example case:
    - Generate off of a file with stream delay set to 0
    - Determine delay based off difference of track end and actual end (in audacity or w/e)
    - Try again with new delay and see if it works

Streams seem to average around 6 to 6.3 for stream delay, but you should do the above steps to get it right for each
stream.
"""
import time, os, re
from mutagen.id3 import ID3, TIT2, TPE1, COMM, APIC
from mutagen.easyid3 import EasyID3

EXTENSION = "mp3"
BITRATE_MOD = 1024 / 8
MP3_CORRECTION = 1.024


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


def make_file_name(title, folder, incomplete):
    name = title
    if not name:
        name = "DJ change detected"
    name = re.sub("[<>:\"/\\\|?*]", "", name)
    name = "%s" % os.path.join(folder, name)
    if incomplete:
        name += "_INCOMPLETE"
    name = "%s.%s" % (name, EXTENSION)
    return name


def post_split(folder, delay):
    bloc_name = os.path.join(folder, 'recording_bloc.mp3')
    cue_name = os.path.join(folder, 'cue_file.txt')
    song_list = []
    dj = ''
    dj_dict = {}
    bit_rate = 192
    with open(cue_name, 'r') as cue_file:
        for line in cue_file.readlines():
            split_line = line.split(' ')
            if split_line[0] == "DJ":
                dj = ' '.join(split_line[1:-6])
                dj_dict[dj] = split_line[-1][:-1]
            elif split_line[0] == "COMPLETE":
                song_list.append(Song(' '.join(split_line[1:-1]).decode('utf-8'), split_line[-1], dj.decode('utf-8'),
                                      False))
            elif split_line[0] == "INCOMPLETE":
                song_list.append(Song(' '.join(split_line[1:-1]).decode('utf-8'), split_line[-1], dj.decode('utf-8'),
                                      True))
            elif split_line[0] == "Bitrate:":
                bit_rate = int(split_line[1][:-1])
    with open(bloc_name, 'rb') as b:
        bloc_file = b.read()
    last_start = 0
    index = 1
    for song in song_list:
        if song == song_list[-1]:
            song_data = bloc_file[last_start:]
        else:
            duration = float(song.duration)
            if song == song_list[0]:
                duration += delay
            else:
                duration += 0
            duration = int(duration * bit_rate * BITRATE_MOD / MP3_CORRECTION)
            song_data = bloc_file[last_start:last_start + duration]
            last_start += duration
        file_name = make_file_name("%s. %s" % (index, song.raw_title), folder, song.incomplete)
        with open(file_name, 'wb') as new_song:
            new_song.write(song_data)
        tag_song(song, index, file_name, folder, dj_dict)
        index += 1
    """"
    try:
        os.remove(bloc_name)
    except OSError:
        print "Could not delete bloc file"
    """

if __name__ == '__main__':
    target_folder = raw_input("Enter folder: ")
    target_delay = float(raw_input("Enter delay: "))

    post_split(target_folder, target_delay)
