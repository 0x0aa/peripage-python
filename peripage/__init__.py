# peripage-python - python library for peripage thermal printers
# Copyright (C) 2020-2023  bitrate16 (pegasko)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


__title__ = 'Peripage buetooth printing utility'
__version__ = '1.2'
__author__ = 'bitrate16'
__license__ = 'GPLv3'
__copyright__ = 'Copyright (c) GPLv3 2021-2023 bitrate16 (pegasko)'


import time
import zlib
import qrcode
import typing
import enum
import bluetooth

import PIL.Image
import PIL.ImageOps


# Safety cap for waiting on the printer acknowledgement. This is not a user
# pacing option; image data is already paced by Bluetooth backpressure.
PRINT_ACK_TIMEOUT = 10.0


class PrinterTypeSpecs:
    """
    Specification parameters for each printer model. required for unifying the
    printing interface and easy adding new printe models.

    Defines:
    * `row_bytes` - bytes per row encoding
    * `row_width` - width of single row in pixels
    * `row_characters` - width of row in ASCII-mode characters
    * `compressed` - whether the model accepts the compressed `1f00` image
      frame (raw deflate stream produced by the vendor `libCode.so`)
    """

    def __init__(self, row_bytes: int, row_width: int, row_characters: int, compressed: bool=False):
        self.row_bytes = row_bytes
        self.row_width = row_width
        self.row_characters = row_characters
        self.compressed = compressed

class PrinterType(enum.Enum):
    """
    Defines names for supported printer types.
    Currently supported printers are: Peripage A6, A6+, A40, A40+
    """

    A6 = PrinterTypeSpecs(
        row_bytes=48,
        row_width=384,
        row_characters=32
    )

    A6p = PrinterTypeSpecs(
        row_bytes=72,
        row_width=576,
        row_characters=48
    )

    A40 = PrinterTypeSpecs(
        row_bytes=216,
        row_width=1728,
        row_characters=144,
        compressed=True
    )

    A40p = PrinterTypeSpecs(
        row_bytes=231,
        row_width=1848,
        row_characters=154,
        compressed=True
    )

    @classmethod
    def names(cls) -> typing.List[str]:
        """List available keys from Enum"""
        return [ e.name for e in cls ]

    def __new__(cls, *args, **kwds):
        value = len(cls.__members__) + 1
        obj = object.__new__(cls)
        obj._value_ = value
        return obj

    def __init__(self, spec: PrinterTypeSpecs):
        self.spec = spec

class Printer:
    """
    This class defines the Peripage interface utility.
    It contains methods wrapping requests with special control opcodes.
    By default instance of this class is constructed with timeout set
    to 1s and printer type A6.
    Currently there is no thermal overheat protection opcodes found, so
    use printing carefully and avoid overheating of the printer which
    may result in hardware break.
    Currently there is no stop codes found, so you can not stop printing.

    It is required to perform reset() after connection to the printer.
    """

    @staticmethod
    def filter_ascii(text: str) -> str:
        """
        Remove non-safe-ascii letters from the string so it can be safety used
        in most internal calls.
        """

        return ''.join([ i for i in text if (31 < ord(i) or ord(i) == 10) and ord(i) < 127 ])

    @staticmethod
    def is_safe_ascii(text: str) -> bool:
        """
        Check is string does not contain non-safe-ascii letters (like `>0x7f` or `\\0`).
        """

        for i in text:
            if not ((31 < ord(i) or ord(i) == 10) and ord(i) < 127):
                return False
        return True

    def __init__(self, mac: str, printer_type: PrinterType, timeout: float=1.0, block_size: int=1024):
        """
        Create instance of peripage connector. `mac` and `printer_type` are
        required for bluetooth connection and printer-specific printing
        parameters.

        In order to make printer operate normally, it is required to call
        `reset()` after connecting.

        Arguments:
        * `mac` - mac address of the printer
        * `printer_type` - printer type enum with specification
        * `timeout` - socket connection timeout in seconds
        * `block_size` - image write block size
        """

        self.mac = mac
        self.timeout = timeout
        self.printer_type = printer_type
        self.block_size = block_size

        # buffer used for continuous printing with line wrapping
        self.print_buffer = ''

    def isConnected(self) -> bool:
        """
        Check if printer is connected (socket alive)
        """

        try:
            self.sock.getpeername()
            return True
        except:
            return False

    def connect(self) -> None:
        """
        Open a new connection to the printer without checking for existing
        connection. In case of malfunction and/or twice connecting to the same
        printer, socket descriptor becomes unoperateable.

        In order to make printer operate normally, it is required to call
        `reset()` after connecting.
        """

        self.sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
        self.sock.connect((self.mac, 1))
        self.sock.settimeout(self.timeout)

    def reconnect(self) -> None:
        """
        Reconnect to the printer with existing connection check.

        In order to make printer operate normally, it is required to call
        `reset()` after connecting.
        """

        if self.isConnected():
            # self.sock.shutdown(socket.SHUT_RDWR)
            self.sock.close()
            del self.sock

        self.sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
        self.sock.connect((self.mac, 1))
        self.sock.settimeout(self.timeout)

    def disconnect(self) -> None:
        """
        Disconnect from the printer.
        """

        if self.isConnected():
            # self.sock.shutdown(socket.SHUT_RDWR)
            self.sock.close()
            del self.sock

    def setTimeout(self, timeout) -> None:
        """
        Set the bluetooth socket connection recv / send timeout.
        """

        self.timeout = timeout
        if self.isConnected():
            self.sock.settimeout(timeout)

    def tellPrinter(self, byteseq: bytes) -> None:
        """
        Send `bytes` to the printer without response.

        Arguments:
        * `byteseq` - `bytes` data
        """

        self.sock.send(byteseq)

    def askPrinter(self, byteseq: bytes, recv_size: int=1024) -> bytes:
        """
        Send `bytes` to the printer with response.

        Arguments:
        * `recv_size` - max size of received chunk
        * `byteseq` - `bytes` data
        """

        self.sock.send(byteseq)
        return self.sock.recv(recv_size)

    def listenPrinter(self, recv_size: int=1024) -> bytes:
        """
        Receive data from printer.

        Arguments:
        * `recv_size` - max size of received chunk
        """

        return self.sock.recv(recv_size)

    def _sendBlocks(self, data: bytes) -> None:
        """
        Write `data` in `block_size` blocks without fixed per-row delays.

        This mirrors the Android client, which writes image frames in 1024-byte
        chunks and relies on RFCOMM backpressure plus the printer completion
        marker instead of sleeping after every row.
        """

        total = len(data)

        for offset in range(0, total, self.block_size):
            block = data[offset:offset + self.block_size]
            self.tellPrinter(block)

    def _waitForPrintDone(self) -> bool:
        """
        Wait for the printer's acknowledgement (`OK` or `0xAA`).

        Returns `True` when an acknowledgement is seen and `False` on the
        internal safety timeout.
        """

        deadline = time.monotonic() + PRINT_ACK_TIMEOUT
        previous_timeout = self.timeout

        try:
            self.sock.settimeout(0.2)

            while time.monotonic() < deadline:
                try:
                    data = self.sock.recv(256)
                except Exception:
                    continue

                if not data:
                    continue

                if b'\xaa' in data:
                    return True

            return False
        finally:
            try:
                self.sock.settimeout(previous_timeout)
            except Exception:
                pass

    def tellPrinterSeq(self, byteseq: typing.Iterable[bytes]) -> None:
        """
        Send list of `bytes` to the printer without response.

        Arguments:
        * `byteseq` - `list` of `bytes`
        """

        for s in byteseq:
            self.sock.send(s)

    def askPrinterSeq(self, byteseq: typing.Iterable[bytes], recv_size: int=1024) -> bytes:
        """
        Send list of `bytes` to the printer with response.

        Arguments:
        * `recv_size` - max size of received chunk
        * `byteseq` - `list` of `bytes`
        """

        for s in byteseq:
            self.sock.send(s)
        return self.sock.recv(recv_size)

    def getDeviceModel(self) -> bytes:
        """
        Query the printer model string.

        Request: `10ff20f0`.

        Response: `bytes` with model name.

        Example: Peripage A6+ returns `IP-300`.

        Note: this command was previously exposed as `getDeviceIP()`, but the
        Android client uses it for the model name.
        """

        return self.askPrinter(bytes.fromhex('10ff20f0'))

    def getDeviceIP(self) -> bytes:
        """
        Deprecated alias of `getDeviceModel()`.

        The A6+ answer `IP-300` is a model / firmware platform identifier, not
        an IP address.
        """

        return self.getDeviceModel()

    def getDeviceBluetoothName(self) -> bytes:
        """
        Query the Bluetooth module name.

        Request: `10ff3101`.
        """

        return self.askPrinter(bytes.fromhex('10ff3101'))

    def getDeviceBluetoothVersion(self) -> bytes:
        """
        Query the Bluetooth module version.

        Request: `10ff3103`.
        """

        return self.askPrinter(bytes.fromhex('10ff3103'))

    def getDeviceName(self) -> bytes:
        """
        Query device name.

        Request: `10ff3011`.

        Response: `bytes` with `device_name+two_bytes_of_mac`

        Example: Peripage A6+ returns `PeriPage+DF7A`.
        """

        return self.askPrinter(bytes.fromhex('10ff3011'))

    def getDeviceSerialNumber(self) -> bytes:
        """
        Query serial number.

        Request: `10ff20f2`.

        Response: `bytes` with serial number

        Example: Peripage A6+ returns `A6491571121`.
        """

        return self.askPrinter(bytes.fromhex('10ff20f2'))

    def getDeviceFirmware(self) -> bytes:
        """
        Query device firmware version.

        Request: `10ff20f1`.

        Response: `bytes` with firmware version

        Example: Peripage A6+ returns `V2.11_304dpi`.
        """

        return self.askPrinter(bytes.fromhex('10ff20f1'))

    def getDeviceBattery(self) -> int:
        """
        Query device battery percentage.

        Request: `10ff50f1`.

        Response: `bytes[2]` with percentage. `bytes[2] = { 0, percentage }`

        Example: Peripage A6+ returns `\\x00@` (equals to `bytes[2] = { 0, 64 }`).
        """
        return int(self.askPrinter(bytes.fromhex('10ff50f1'))[1])

    def getDeviceHardware(self) -> bytes:
        """
        Query device hardware info.

        Request: `10ff3010`.

        Response: `bytes` with hw info.

        Example: Peripage A6+ returns `BR2141e-s(A02)_B9_20190815_r3460`.
        `BR2141e-s` chip with a pile of ascii letters.
        """

        return self.askPrinter(bytes.fromhex('10ff3010'))

    def getDeviceMAC(self) -> bytes:
        """
        Query device mac from device itself.

        Request: `10ff3012`.

        Response: `bytes` with mac address.

        Example: Peripage A6+ returns `\\x00\\xF5\\x73\\x25\\xAC\\x9F_\\x00\\xF5\\x73\\x25\\xAC\\x9F_`
        (equals to `00:F5:73:25:AC:9F`).
        """

        return self.askPrinter(bytes.fromhex('10ff3012'))

    def getDeviceFull(self) -> bytes:
        """
        Query full device info.

        Request: `10ff70f100`.

        Response: `bytes` with fill info.

        Example: Peripage A6+ returns `PeriPage+DF7A|00:F5:73:25:AC:9F|C5:12:81:19:2C:51|V2.11_304dpi|A6491571121|84`
        (`name+mac_slice|device_mac|client_mac|firmware|serial_number|battery_percentage`).

        WARNING:

        This command has a side-effect causing the printed images getting
        corrupted by shifting horisontally and adding a █ character to the
        in-printer ASCII buffer.
        """

        return self.askPrinter(bytes.fromhex('10ff70f100'))

    def getRowBytes(self) -> int:
        """
        Get row_bytes spec for current printer.

        Images are encoded as 1-pixel-per-1-bit, which means that 1 byte can
        encode 8 black-white pixels in a line. This property defines the bytes
        limit per image row, the overflow is truncated.
        """

        return self.printer_type.spec.row_bytes

    def getRowWidth(self) -> int:
        """
        Get row_width spec for current printer.

        Images are encoded as 1-pixel-per-1-bit, which means that 1 byte can
        encode 8 black-white pixels in a line. This property defines the pixel
        limit per image row, the overflow is truncated.
        """

        return self.printer_type.spec.row_width

    def getRowCharacters(self) -> int:
        """
        Get row_characters spec for current printer.

        Internal ASCII printing mode allows printer to output the raw ASCII
        letters up to `0x7f` and below to `0x10`. This property defunes the
        amount of letters that can fit in a single row. in case of wrapped
        printing, the overflow is wrapped using in-class buffer and synchronized
        with the in-printer buffer.
        """

        return self.printer_type.spec.row_characters

    def getHeightLimit(self) -> int:
        """
        Get the limit of single-image printing chunk.

        Printer protocol allows only 16-bit number as the definition for the
        image printing procedire, that requires used to vertically split the
        image into multiple chunks.
        """

        return 0xffff

    def setDeviceSerialNumber(self, serial_number: str, wait: bool=True) -> None:
        """
        Set device serial number.

        Set a new device serial number explicitly. `serial_number` defines the
        new serial number for the device. This serial number must be
        ascii-encodable string that match the requirements of
        `Printer.is_safe_ascii()` filter in order to work. Serial number string
        is additionally filtered with `Printer.filter_ascii` if you haven't read
        the previous sentence.

        Request: `10ff20f4+ascii_str+00`.

        Arguments:
        * `serial_number` - serial number string that passes the
        `Printer.is_safe_ascii()` check.
        """

        request = bytes.fromhex('10ff20f4') + Printer.filter_ascii(serial_number).encode('ascii') + b'\0'

        if wait:
            return self.askPrinter(request)
        else:
            self.tellPrinter(request)

    def setPowerTimeout(self, timeout: int, wait: bool=True) -> None:
        """
        Set device poweroff timeout.

        Device standby mode is triggered by any action made with the device. It
        can be either a print task, battery lever query and anything else that
        envolves ask-answer communication. Power timeout defines the internal
        auto poweroff timeout of the device in minutes, up to `0xffff` minutes.

        Request: `10ff12+bytes[2]:big_endian`.

        Arguments:
        * `timeout` - new timeout value between `0` and `0xffff`, minutes
        """

        timeout = max(min(0xfff0, timeout), 0x0001)
        request = bytes.fromhex('10ff12') + int.to_bytes(timeout, 2, 'big')

        if wait:
            return self.askPrinter(request)
        else:
            self.tellPrinter(request)

    def setConcentration(self, concentration: int, wait: bool=False) -> None:
        """
        Set printing concentration level.

        Printer supports multiple temperature concentration modes that allow to
        print darker or lighter images with the price of overheating. The more
        concentration - the longer lasting image will be.

        Request: `10ff1000+bytes[1]:big_endian`.

        Arguments:
        * `concentration` - concentration value from range `(0, 1, 2)`
        """

        if concentration <= 0:
            request = bytes.fromhex('10ff100000')
        elif concentration == 1:
            request = bytes.fromhex('10ff100001')
        elif concentration >= 2:
            request = bytes.fromhex('10ff100002')

        if wait:
            return self.askPrinter(request)
        else:
            self.tellPrinter(request)

    def reset(self) -> None:
        """
        Send reset request, required for initial printer initialization after
        connect/reconnect. Without this operation, printer will not print nor
        return any data.

        This is the combined form of `wake()` (`10fffe01`) and
        `resetBuffer()` (twelve zero bytes), which the Android client sends as
        two separate writes before every print job.

        Request: `10fffe01+000000000000000000000000`.
        """

        self.tellPrinter(bytes.fromhex('10fffe01000000000000000000000000'))

    def wake(self) -> None:
        """
        Wake / unlock the printer engine.

        Request: `10fffe01`.

        The Android client sends this before `resetBuffer()` and the image
        data. `reset()` already contains the same bytes.
        """

        self.tellPrinter(bytes.fromhex('10fffe01'))

    def resetBuffer(self) -> None:
        """
        Clear the in-printer image buffer (twelve zero bytes).

        The Android client sends this immediately after `wake()` and before
        each image.
        """

        self.tellPrinter(bytes(12))

    def finishPrint(self) -> None:
        """
        Finish the current print job.

        Request: `10fffe45`.

        The Android client sends this after an image has been transferred, so
        the printer can return to idle and flush its internal state.
        """

        self.tellPrinter(bytes.fromhex('10fffe45'))

    def positionPaper(self) -> None:
        """
        Advance/position the paper after an image (used by label modes).

        Request: `1d0c`.
        """

        self.tellPrinter(bytes.fromhex('1d0c'))

    def feedBack(self, dots: int=32) -> None:
        """
        Back the paper up by `dots` dots (used to compensate for paper pulled
        forward when a job starts).

        Request: `10ff81+bytes[1]`.

        Arguments:
        * `dots` - number of dots, clamped to `0..120`.
        """

        dots = min(120, max(0, dots))
        self.tellPrinter(bytes.fromhex('10ff81') + int.to_bytes(dots, 1, 'big'))

    def setPaperType(self, paper_type: int) -> None:
        """
        Select the loaded paper type.

        Request: `10ff1003+bytes[1]`.

        Arguments:
        * `paper_type` - paper type: `0` continuous, `1` black mark, `2` gap
          label, `3` page, `4` tattoo.
        """

        paper_type = min(4, max(0, paper_type))
        self.tellPrinter(bytes.fromhex('10ff1003') + int.to_bytes(paper_type, 1, 'big'))

    def setPrintSpeed(self, speed: int) -> None:
        """
        Set the print speed of P90-family printers.

        Request: `10ffc0+bytes[1]`.

        Arguments:
        * `speed` - print speed from `0` to `9`.
        """

        speed = min(9, max(0, speed))
        self.tellPrinter(bytes.fromhex('10ffc0') + int.to_bytes(speed, 1, 'big'))

    def getDeviceStatus(self) -> bytes:
        """
        Query the printer status.

        Request: `10ff40`.

        Response: two bytes. The first byte is a bit field:
        * `0x10` - cover opened
        * `0x08` - printing
        * `0x04` - battery low
        * `0x01` - no paper

        Use `Printer.parseDeviceStatus()` to turn the first byte into text.
        """

        return self.askPrinter(bytes.fromhex('10ff40'))

    @staticmethod
    def parseDeviceStatus(status: bytes) -> str:
        """
        Convert the first status byte returned by `getDeviceStatus()` into a
        human-readable string. Returns `Unknown` for an empty response.
        """

        if not status:
            return 'Unknown'

        flags = status[0]
        if flags == 0:
            return 'OK'

        states = []
        if flags & 0x10:
            states.append('CoverOpened')
        if flags & 0x01:
            states.append('NoPaper')
        if flags & 0x08:
            states.append('Printing')
        if flags & 0x04:
            states.append('BatteryLow')

        return '|'.join(states) if states else 'Unknown(0x%02x)' % flags

    def printBreak(self, size: int=0x40) -> None:
        """
        Ask printer to print out a break of fixed size.

        Printer allows user to feed out some paper to wipe away tears of this
        module developer.

        Request: `1b4a+bytes[1]:big_endian`.

        Arguments:
        * `size` - break size in range `(0, 0xff)`
        """

        size = min(0xff, max(0x01, size))
        request = bytes.fromhex('1b4a') + int.to_bytes(size, 1, 'big')

        self.tellPrinter(request)

    def writeASCII(self, text: str='\n', wait=False) -> None:
        """
        WARNING: THIS API IS UNSAFE

        Write text into printer without internal safety-checks and filtering. If
        you want to print text with internal checks for non-ascii or
        unsafe-ascii characters, use `Printer.printASCII()`. If you need to use
        this function, check you text with `Printer.is_safe_ascii` or filter
        with `Printer.filter_ascii` and do not leave more than one sequential
        `\\n` character.

        Request: `ascii_str`.

        Arguments:
        * `text` - text to be printed, should be checked by user before print or
        may malfunction and/or damage the printer. String must not contain
        repeating `\\n` characters or printer will freeze.
        """

        request = text.encode('ascii')

        if wait:
            return self.askPrinter(request)
        else:
            self.tellPrinter(request)

    def printlnASCII(self, text: str='', delay: float=0.25) -> None:
        """
        Safe to use printing method that relies on in-class buffer for wrapping
        text. The input is filtered with `Printer.filter_ascii` in order to
        exclude all non-safe-ascii characters and later splitted into multiple
        chunks over `\\n` in order to prevent freeze caused by twice-newline in
        printer buffer. This function is equal to  normal `println` in C and
        semi-equal to `print(text + '\\n')`. This method relies on in-class
        buffer to track printed data and keeping sync with in-printer buffer.

        Request: `impl:Printer.printASCII()`.

        Arguments:
        * `text` - text to be printed, automatically filtered with
        `Printer.filter_ascii()` and splitted into newline-chunked data.
        * `delay` - delay between lines submission, seconds
        """

        self.printASCII(text=text + '\n', delay=delay)

    def printASCII(self, text: str='\n', delay: float=0.25) -> None:
        """
        Safe to use printing method that relies on in-class buffer for wrapping
        text. The input is filtered with `Printer.filter_ascii` in order to
        exclude all non-safe-ascii characters and later splitted into multiple
        chunks over `\\n` in order to prevent freeze caused by twice-newline in
        printer buffer. This function is equal to  normal `print` in C. This
        method relies on in-class buffer to track printed data and keeping sync
        with in-printer buffer.

        In case when the input text contains two sequential `\\n`, they are
        replaced with `Printer.printBreak(30)`.

        Request: `impl:Printer.writeASCII()`.

        Arguments:
        * `text` - text to be printed, automatically filtered with
        `Printer.filter_ascii()` and splitted into newline-chunked data.
        * `delay` - delay between lines submission, seconds
        """

        text = Printer.filter_ascii(text)

        # Check for empty and print out newline
        text = self.print_buffer + text
        self.print_buffer = ''
        if len(text) == 0:
            return

        # Special case: \n only, causes duplicating newlines (white-only string)
        if len(text.strip()) == 0:
            for s in text:
                if s == '\n':
                    self.printBreak(30)
                    time.sleep(delay)
            return

        # Iterlines
        lines = text.split('\n')
        for l in lines:

            # Flush previuos incomplete line
            if len(self.print_buffer) != 0:
                self.tellPrinter(self.print_buffer.encode('ascii'))
                self.tellPrinter(b'\n')
                self.print_buffer = ''
                time.sleep(delay)

            # Flush if white-empty, because it is newline
            elif len(l.strip()) == 0:

                # Flush in-printer buffer if not empty
                if len(self.print_buffer) != 0:
                    self.tellPrinter(self.print_buffer.encode('ascii'))
                    self.tellPrinter(b'\n')
                    self.print_buffer = ''
                    time.sleep(delay)

                # Trail
                else:
                    self.printBreak(30)
                    time.sleep(delay)

            # Process normal lines
            else:
                # Wrap line
                parts = [ l[i:i+self.getRowCharacters()] for i in range(0, len(l), self.getRowCharacters()) ]

                for p in parts:

                    # Print full line
                    if len(p) == self.getRowCharacters():
                        self.tellPrinter(p.encode('ascii'))
                        self.tellPrinter(b'\n')
                        time.sleep(delay)

                    # Partial, write to buffer
                    else:
                        self.print_buffer = p

    def flushASCII(self, delay: float=0.25) -> None:
        """
        Force=print out buffer if it is not empty. Not equal to
        `Printer.println()` because does not output empty newline if buffer is
        empty.

        Request: `impl:Printer.printASCII()`.

        Arguments:
        * `delay` - delay between lines submission, seconds
        """

        if len(self.print_buffer) != 0:
            self.tellPrinter(self.print_buffer.encode('ascii'))
            self.tellPrinter(b'\n')
            self.print_buffer = ''
            time.sleep(delay)

    def printRow(self, rowbytes: bytes) -> None:
        """
        Send bytes representing a single image row in binary black/white mode.
        If amount of bydes exceedes the `Printer.getRowBytes()` constant, input
        is truncated. If size of input is under the `Printer.getRowBytes()`, it
        will be padded with zeros.

        Request: `1d763000+bytes[2]:big_endian+0100+bytes[Printer.getRowBytes()*1]`.

        Note: In case of A6+, preamble is `1d76300048000100` that can be viewed
        as `[ 1d7630, 0030, 0001 ]`, where `1d7630` is printing operation
        request, `0030` is big endian bytes per row, `0001` is big endian input
        height.

        Arguments:
        * `rowbytes` - bytes representing image pixels, 8 pixels per byte,
        truncated/padded to fit `Printer.getRowBytes()`.
        """

        expectedLen = self.getRowBytes()
        if len(rowbytes) < expectedLen:
            rowbytes = rowbytes.ljust(expectedLen, b'\0')
        elif len(rowbytes) > expectedLen:
            rowbytes = rowbytes[:expectedLen]

        self.reset()

        # Notify printer about incomming $expectedLen bytes row
        request = bytes.fromhex('1d763000') + int.to_bytes(self.getRowBytes(), 1, 'big') + bytes.fromhex('000100') + rowbytes
        self.tellPrinter(request)
        self._waitForPrintDone()

    def printRowBytesList(self, rowbytes: typing.Iterable[bytes]) -> None:
        """
        Send an array of bytes representing a multiple image rows in binary
        black/white mode. If amount of bydes per row exceedes the
        `Printer.getRowBytes()` constant, input is truncated. If size of input
        is under the `Printer.getRowBytes()`, it will be padded with zeros.

        The image header carries a 16-bit height, so the input is sliced into
        chunks of at most `0xffff` rows. Each chunk gets a fresh `reset()`.

        Note: the preamble is
        `1d 76 30 <mode> <row_bytes:le16> <height:le16>`, where `1d7630` is the
        printing operation request, `mode` is 0 for normal printing and
        `row_bytes` is `Printer.getRowBytes()`. The Android client also uses
        modes 1..3 for its faster image paths.

        Request: chunked `1d763000+bytes[1]:big_endian+00+bytes[2]:little_endian+bytes[Printer.getRowBytes()*chunk_height]`.

        Arguments:
        * `rowbytes` - list of bytes defining each row of the image. If row
        length does not match the `Printer.getRowBytes()`, data is
        truncated/padded to match the size.
        """

        if len(rowbytes) == 0:
            return

        expectedLen = self.getRowBytes()
        chunks = [ rowbytes[i:i+0xffff] for i in range(0, len(rowbytes), 0xffff) ]

        for chunk in chunks:

            # Reset state before print
            self.reset()

            #          1d763000    30                    00     height:le16
            # preamble: operation + bytes-per-row:le16 + height:le16
            request = bytes.fromhex('1d763000') + int.to_bytes(self.getRowBytes(), 1, 'big') + bytes.fromhex('00') + int.to_bytes(len(chunk), 2, 'little')

            payload = b''.join(
                (row[:expectedLen] if len(row) > expectedLen else row.ljust(expectedLen, b'\0'))
                for row in chunk
            )

            self._sendBlocks(request + payload)
            self._waitForPrintDone()

    def printRowBytesIterator(self, rowiterator: typing.Iterable[bytes]) -> None:
        """
        Iterate over the given iterator and print out all produced rows. This
        method is very slow as it required printer to oftenly switch on/off
        printing mode and pass a large overhead to set up the printing mode.

        This method uses the `Printer.printRow()` call.

        Arguments:
        * `rowiterator` - iterator that returns bytes.
        """

        for r in rowiterator:
            self.printRow(r)

    def printRowChunksIterator(self, rowiterator: typing.Iterable[typing.List[bytes]]) -> None:
        """
        Iterate over the given iterator and print out all produced chunks of
        rows. One chunk of rows is a list of bytes where each bytes define the
        specific line of the image. This method is better than the use of
        `Printer.printRowBytesIterator()` as it passes each chunk of image data
        directly into the `Printer.printImageRowBytesList()`.

        Arguments:
        * `rowiterator` - iterator that returns list[bytes].
        """

        for chunk in rowiterator:
            self.printRowBytesList(chunk)

    def printImageBytes(self, imagebytes: bytes) -> None:
        """
        Send an bytes representing single-line encoded image. For example,
        `[0xff000000, 0x00ff0000, 0x0000ff00, 0x000000ff]` is encoded as
        `0xff00000000ff00000000ff00000000ff`.

        Image must be valid aligned and sequence size must divide by
        `Printer.getRowBytes()`. In case of partial data, the rest of partial
        data is padded with zeros. Number of lines is calcualted as
        `nlines = ceil(len(imagebytes) / Printer.getRowBytes())`.

        Arguments:
        * `imagebytes` - bytes defining concatenated rows of the image. Each
        row must be aligned to `Printer.getRowBytes()` in order to display
        properly. If length of the last row dows not match
        `Printer.getRowBytes()`, data is truncated/padded to match the size.
        """

        if len(imagebytes) == 0:
            return

        # Delegate to impl
        self.printRowBytesList([ imagebytes[i:i+self.getRowBytes()] for i in range(0, len(imagebytes), self.getRowBytes()) ])
        self.finishPrint()

    def printCompressedBytes(self, imagebytes: bytes) -> None:
        """
        Send raw 1-bpp row data using the compressed `1f00` image frame.

        This is the fast path used by the current Android client for A40,
        A40+, A9, P40 and other newer models. The payload is a normal zlib
        stream with its two-byte zlib header removed, which is exactly what
        the vendor `libCode.so::code()` produces.

        Frame layout:
        `1f 00 <row_bytes:be16> <height:be16> <payload_len:be32> <payload>`

        Arguments:
        * `imagebytes` - concatenated 1-bpp rows, each `getRowBytes()` long.
        """

        if not imagebytes:
            return

        expected_len = self.getRowBytes()
        if len(imagebytes) % expected_len != 0:
            padded_len = ((len(imagebytes) + expected_len - 1) // expected_len) * expected_len
            imagebytes = imagebytes.ljust(padded_len, b'\0')

        total_rows = len(imagebytes) // expected_len
        if total_rows == 0:
            return

        for row_start in range(0, total_rows, 0xffff):
            row_count = min(0xffff, total_rows - row_start)
            chunk = imagebytes[row_start * expected_len:(row_start + row_count) * expected_len]

            compressed = zlib.compress(chunk, 6)
            payload = compressed[2:]

            request = bytes.fromhex('1f00') \
                + int.to_bytes(expected_len, 2, 'big') \
                + int.to_bytes(row_count, 2, 'big') \
                + int.to_bytes(len(payload), 4, 'big') \
                + payload

            self.reset()
            self._sendBlocks(request)
            self._waitForPrintDone()

        self.finishPrint()

    def printImageCompressed(self, img: PIL.Image.Image, resample=PIL.Image.Resampling.NEAREST) -> None:
        """
        Print a PIL image using the compressed image frame.

        This is automatically selected by `printImage()` for printer types
        whose specification has `compressed=True`.

        Arguments:
        * `img` - image to print.
        * `resample` - resampling mode used while scaling to printer width.
        """

        img = self._prepareImage(img, resample)
        self.printCompressedBytes(img.tobytes())

    def _prepareImage(self, img: PIL.Image.Image, resample=PIL.Image.Resampling.NEAREST) -> PIL.Image.Image:
        """
        Convert an arbitrary PIL image to the printer's 1-bpp, MSB-first row
        format, scaled to `Printer.getRowWidth()`.
        """

        img = img.convert('L')
        img = PIL.ImageOps.invert(img)
        img = img.resize((self.getRowWidth(), int(self.getRowWidth() / img.size[0] * img.size[1])), resample)
        return img.convert('1')

    def printImage(self, img: PIL.Image.Image, resample=PIL.Image.Resampling.NEAREST, compressed: typing.Optional[bool]=None) -> None:
        """
        Print PIL Image on this printer with automatic internal to-blackwhite
        conversion.

        WARNING: In order to prevent the overhead of the printer (and possibly
        loose some data but to limitations of the in-printer buffer) it is
        suggested to split image into many vertical pieces and wait a
        reasonable amount of time to let the printer to cooldown.

        Arguments:
        * `img` - your pretty PIL Image.
        * `resample` - resampling mode of the image, used to automatically
        rescale image to fit the printer width of `Printer.getRowWidth()`.
        * `compressed` - force (`True`) or disable (`False`) the compressed
        `1f00` frame. `None` selects it automatically from the printer spec
        (A40/A40+ use it by default).
        """

        if compressed is None:
            compressed = self.printer_type.spec.compressed

        img = self._prepareImage(img, resample)

        if compressed:
            self.printCompressedBytes(img.tobytes())
        else:
            self.printImageBytes(img.tobytes())

    def printImageIterator(self, imgiterator: typing.Iterable[PIL.Image.Image]):
        """
        Iterate over iterator and print out each PIL Image that it returns.

        Arguments:
        * `rowiterator` - iterator that returns list[bytes].
        """

        for img in imgiterator:
            self.printImage(img)

    def printQR(self, text: str, resample=PIL.Image.Resampling.NEAREST, compressed: typing.Optional[bool]=None) -> None:
        """
        Generate a QR code from specified string and print it.

        Arguments:
        * `text` - your pretty text.
        * `resample` - resampling mode of the image, used to automatically
        rescale image to fit the printer width of `Printer.getRowWidth()`.
        * `compressed` - force/disable the compressed `1f00` image frame.
        `None` selects it automatically from the printer spec.
        """

        self.printImage(qrcode.make(text, border=0), resample=resample, compressed=compressed)
