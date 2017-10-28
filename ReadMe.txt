Stream Saver (tmp)

The purpose of this script is to download internet radio streams to your computer, specifically to replace the stream_ripper program that is no longer updated.

While this currently only supports Icecast 2.x streams in mp3, in future builds it should be able to support more standards such as Shoutcast, as well as change the
audio extension to whatever the stream is using.

Plans:
	- Automatically switch file extensions
	- Support more stream standards
	- More cmd arg options
		+ A no-stream option, only generate list of what was streamed
		+ Don't split files
		+ Order files through tagging
		+ Improved tagging (DB lookup?)
		+ GUI (?)
		+ quit during recording loop elegantly
		+ auto-fix errors (start again after errors rather than quit)
		+ continue audio files rather than quit/overwrite

CMD use:
	-load config_name
		+ loads a config of config_name from the config.json file
		+ no default
	-save config_name
		+ saves the current config (after all args are parsed) to config.json under config_name
		+ no default
	-timeout n
		+ sets the global timeout for any spinlocks to n seconds
		+ defaults to 20
	-file_path foo/bar
		+ sets the file path to store files/folders, use / or C:\ to specify root otherwise it will assume local dir
		+ defaults to the current dir
	-file_check_interval
		+ the interval in seconds at which the server is pinged for a change in song title
		+ defaults to 1
	-dj_url
		+ the website url to scrape the DJs name from
		+ defaults to none
	-dj_element
		+ the page element to scrape for the DJs name
		+ defaults to none