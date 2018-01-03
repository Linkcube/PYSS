Python Stream Saver

This is a simple internet radio downloader, currently not stable enough so there's probably better alternatives. This is mainly just to keep track of progress and whatnot.

Currently supports Icecast 2.x

CMD use:
+	load config_name


		 loads a config of config_name from the config.json file
		 no default
+	save config_name


		 saves the current config (after all args are parsed) to config.json under config_name
		 no default
+	timeout n



		 sets the global timeout for any spinlocks to n seconds
		 defaults to 20
+	file_path foo/bar



         sets the file path to store files/folders, use / or C:\ to specify root otherwise it will assume local dir
		 defaults to the current dir
+	file_check_interval



		 the interval in seconds at which the server is pinged for a change in song title
		 defaults to 1
+	dj_url



		 the website url to scrape the DJs name from
		 defaults to none
+	dj_element



		the page element to scrape for the DJs name
		 defaults to none
+	cue_only



		Doesn't record any of the audio stream, only a text file of what songs and dj's appeared.
		defaults to False