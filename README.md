# Joplin-Migration
Migrate Google Keep and Dokuwiki to Joplin

**
Google Keep → Joplin (Greek UTF-8 Supported)**

Hello,

This guide explains how to import notes from Google Keep into Joplin.

I’m sharing this because the Google Keep plugin is not supported in Joplin Desktop.
The procedure converts Google Keep notes into an Evernote (.enex) export file, which can then be imported into Joplin.

The script requires Python ≥ 3.7 and the parsedatetime library.

Requirements
pip install parsedatetime

Steps

Go to Google Takeout and request an export of Google Keep.
Download the generated archive.

Extract the archive and navigate to the Keep folder.

Place the Python script in this folder and run:

python3 keep-to-enex.py Keep/*.html -o output.enex


This will generate an Evernote (.enex) file.

Import the .enex file into Joplin:
File → Import → ENEX (Evernote)

Python Script

GitHub repository:
https://github.com/athinaok/Joplin-Migration/blob/main/google%20keep%20to%20evernote%20convert%20%20%20-%20%20keep-to-enex.py

Notes

Greek (UTF-8) characters are fully supported.

The original script is based on:
https://gitlab.com/charlescanato/google-keep-to-evernote-converter

This version adds support for external images, which the original did not handle.


Joplin Forum 
https://discourse.joplinapp.org/t/migration-google-keep-evernote-and-then-joplin/48166
