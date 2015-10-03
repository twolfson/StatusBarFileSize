import gzip
import io
import os.path
import sublime
import sublime_plugin


# Format a size in bytes into a nicer string value. Defaults to 1024 convention.
def file_size_str(size, divisor=1024):
    sizes = ["KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB"]
    if size < divisor:
        return "%d %s" % (size, "Bytes" if size != 1 else "Byte")
    else:
        size_val = size / divisor
        for unit in sizes:
            if size_val >= divisor:
                size_val /= divisor
            else:
                return "%.2f %s" % (size_val, unit)
        return "%.2f %s" % (size_val * divisor, sizes[-1])


# Far from a perfect system, but seems to be the only way to get a usable Python
# encoding from Sublime Text.
SPECIAL_HEXADECIMAL = "special-hexadecimal"

ENCODING_MAP = {
    "Undefined": "ascii",
    "Hexadecimal": SPECIAL_HEXADECIMAL,
    "UTF-8": "utf-8",
    "UTF-16 LE": "utf-16le",
    "UTF-16 BE": "utf-16be",

    "Western (Windows 1252)": "windows-1252",
    "Western (ISO 8859-1)": "iso-8859-1",
    "Western (ISO 8859-3)": "iso-8859-3",
    "Western (ISO 8859-15)": "iso-8859-15",
    "Western (Mac Roman)": "mac_roman",
    "DOS (CP 437)": "cp-437",

    "Arabic (Windows 1256)": "windows-1256",
    "Arabic (ISO 8859-6)": "iso-8859-6",

    "Baltic (Windows 1257)": "windows-1257",
    "Baltic (ISO 8859-4)": "iso-8859-4",

    "Celtic (ISO 8859-14)": "iso-8859-14",

    "Central European (Windows 1250)": "windows-1250",
    "Central European (ISO 8859-2)": "iso-8859-2",

    "Cyrillic (Windows 1251)": "windows-1251",
    "Cyrillic (Windows 866)": "windows-866",
    "Cyrillic (ISO 8859-5)": "iso-8859-5",
    "Cyrillic (KOI8-R)": "koi8_r",
    "Cyrillic (KOI8-U)": "koi8_u",

    "Estonian (ISO 8859-13)": "iso-8859-13",

    "Greek (Windows 1253)": "windows-1253",
    "Greek (ISO 8859-7)": "iso-8859-7",

    "Hebrew (Windows 1255)": "windows-1255",
    "Hebrew (ISO 8859-8)": "iso-8859-8",

    "Nordic (ISO 8859-10)": "iso-8859-10",

    "Romanian (ISO 8859-16)": "iso-8859-16",

    "Turkish (Windows 1254)": "windows-1254",
    "Turkish (ISO 8859-9)": "iso-8859-9",

    "Vietnamese (Windows 1258)": "windows-1258",
}

CONSTANT_OVERHEAD = {
    # Apparently ST doesn't add BOMs.
    # "UTF-16 LE": b'\xFF\xFE',
    # "UTF-16 BE": b'\xFE\xFF',
}

# Ditto for line endings. At least there's only three forms here.
LINE_ENDINGS_MAP = {
    "Unix": "\n",
    "Windows": "\r\n",
    "CR": "\r",
}

BLOCK_SIZE = 1000


def ranges(start, end, bs):
    i = 0
    while i < end:
        yield (i, min(i + bs, end))
        i += bs


def count_hex_digits(s):
    # Count hexadecimal digits in s.
    return sum(1 for x in s if x in "abcdefABCDEF0123456789")


def iterate_view_blocks(view):
    for start, end in ranges(0, view.size(), BLOCK_SIZE):
        r = sublime.Region(start, end)
        yield r


def estimate_file_size(view):
    tag = view.change_count()

    try:
        line_endings = LINE_ENDINGS_MAP[view.line_endings()]
        encoding = ENCODING_MAP[view.encoding()]
        overhead = CONSTANT_OVERHEAD.get(view.encoding(), '')
    except KeyError:
        # Unknown encoding or line ending, so we fail.
        return None

    size = len(overhead)
    for r in iterate_view_blocks(view):
        if view.change_count() != tag:
            # Buffer was changed, we abort our mission.
            return None
        text = view.substr(r)

        if encoding == SPECIAL_HEXADECIMAL:
            # Special-case handling for the special-case Hexadecimal encoding.
            # The division doesn't truncate on purpose, to count half-bytes when
            # we have uneven numbers of hex digits. The result gets forced into
            # an int on return.
            size += count_hex_digits(text) / 2
        else:
            try:
                size += len(text.replace("\n", line_endings).encode(encoding))
            except UnicodeError:
                # Encoding failed, we just fail here.
                return None

    return int(size)


def calculate_gzip_size(view):
    tag = view.change_count()

    try:
        line_endings = LINE_ENDINGS_MAP[view.line_endings()]
        encoding = ENCODING_MAP[view.encoding()]
        overhead = CONSTANT_OVERHEAD.get(view.encoding(), '')
    except KeyError:
        # Unknown encoding or line ending, so we fail.
        return None

    byte_content = io.BytesIO()
    with gzip.GzipFile(fileobj=byte_content, mode="wb") as f:
        f.write(overhead)
        for r in iterate_view_blocks(view):
            if view.change_count() != tag:
                # Buffer was changed, we abort our mission.
                return None
            text = view.substr(r)

            if encoding == SPECIAL_HEXADECIMAL:
                # TODO: Figure out what to do for hexadecimal
                return None
            else:
                try:
                    f.write(text.replace("\n", line_endings).encode(encoding))
                except UnicodeError:
                    # Encoding failed, we just fail here.
                    return None
    return int(byte_content.tell())


class StatusBarFileSize(sublime_plugin.EventListener):
    KEY_SIZE = "FileSize"
    SETTINGS = "StatusBarFileSize.sublime-settings"
    ESTIMATE_DEFAULT = True
    CALCULATE_GZIP_DEFAULT = False

    @property
    def setting_estimate_file_size(self):
        settings = sublime.load_settings(self.SETTINGS)
        return settings.get("estimate_file_size", self.ESTIMATE_DEFAULT)

    @property
    def setting_calculate_gzip(self):
        settings = sublime.load_settings(self.SETTINGS)
        return settings.get("calculate_gzip", self.CALCULATE_GZIP_DEFAULT)

    def update_file_size(self, view):
        view.erase_status(self.KEY_SIZE)

        size = None
        gzip_size = None
        if not view.file_name() or view.is_dirty():
            if self.setting_estimate_file_size:
                # Try to estimate the file size based on encoding and line
                # endings.
                size = estimate_file_size(view)
                pattern = "~{size}"
                if self.setting_calculate_gzip:
                    gzip_size = calculate_gzip_size(view)
                    gzip_pattern = "~{size} ({gzip_size})"
        else:
            try:
                size = os.path.getsize(view.file_name())
                pattern = "{size}"
                if self.setting_calculate_gzip:
                    # Faster to use content from buffer than reading on disk
                    gzip_size = calculate_gzip_size(view)
                    gzip_pattern = "{size} ({gzip_size})"
            except OSError:
                pass

        if size is not None:
            if gzip_size is not None:
                status = gzip_pattern.format(size=file_size_str(size), gzip_size=file_size_str(gzip_size))
            else:
                status = pattern.format(size=file_size_str(size))
            view.set_status(self.KEY_SIZE, status)

    on_post_save_async = update_file_size
    on_modified_async = update_file_size
    on_activated_async = update_file_size
