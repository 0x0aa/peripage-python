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


def main():
    import argparse
    import sys
    import peripage
    import PIL.Image

    parser = argparse.ArgumentParser(description='Print on a Peripage printer via bluetooth')
    parser.add_argument(
        '-m', '--mac',
        help='Bluetooth MAC address of the printer',
        required=True,
        type=str
    )
    parser.add_argument(
        '-c', '--concentration',
        help='Concentration value for printing (temperature)',
        choices=[0, 1, 2],
        metavar='[0-2]',
        type=int,
        default=0
    )
    parser.add_argument(
        '-b', '--break',
        dest='break_size',
        help='Size of the break inserted after printed image or text',
        choices=range(256),
        metavar='[0-255]',
        type=int,
        default=0
    )
    parser.add_argument(
        '-p', '--printer',
        help='Printer model selection',
        choices=peripage.PrinterType.names(),
        type=str,
        required=True
    )
    parser.add_argument(
        '--paper-type',
        help='Set paper type before printing (0=continuous, 1=black mark, 2=gap label, 3=page, 4=tattoo)',
        choices=range(5),
        metavar='[0-4]',
        type=int,
        default=None
    )
    parser.add_argument(
        '--speed',
        help='Set P90-family print speed before printing (0-9)',
        choices=range(10),
        metavar='[0-9]',
        type=int,
        default=None
    )
    parser.add_argument(
        '--feed-back',
        help='Back-feed paper by N dots before printing (0-120)',
        choices=range(121),
        metavar='[0-120]',
        type=int,
        default=None
    )

    compress_group = parser.add_mutually_exclusive_group()
    compress_group.add_argument(
        '--compressed',
        dest='compressed',
        help='Force compressed image transfer (default for A40/A40+)',
        action='store_true',
        default=None
    )
    compress_group.add_argument(
        '--no-compress',
        dest='compressed',
        help='Force raw row-by-row image transfer (fallback/debug)',
        action='store_false'
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-t', '--text',
        help='ASCII text to print. Text must be ASCII-safe and will be filtered for invalid characters',
        type=str
    )
    group.add_argument(
        '-s', '--stream',
        help='Print text received from STDIN, line by line. Text must be ASCII-safe and will be filtered for invalid characters',
        action='store_true'
    )
    group.add_argument(
        '-i', '--image',
        help='Path to the image for printing',
        type=str
    )
    group.add_argument(
        '-q', '--qr',
        help='String to convert into a QR code for printing',
        type=str
    )
    group.add_argument(
        '-e', '--introduce',
        help='Ask the printer to introduce itself',
        action='store_true'
    )
    group.add_argument(
        '--status',
        help='Query printer status (cover, paper, printing, low-battery flag)',
        action='store_true'
    )
    group.add_argument(
        '--battery',
        help='Query printer battery percentage',
        action='store_true'
    )

    args = parser.parse_args()

    # Open connection
    printer = peripage.Printer(args.mac, peripage.PrinterType[args.printer])
    printer.connect()
    printer.reset()

    def apply_printer_options():
        if args.paper_type is not None:
            printer.setPaperType(args.paper_type)

        if args.speed is not None:
            printer.setPrintSpeed(args.speed)

        if args.feed_back is not None:
            printer.feedBack(args.feed_back)

    # Act based on args
    if 'status' in args and args.status:

        status = printer.getDeviceStatus()
        print(f'status: {status.hex()}')
        print(f'state:  {peripage.Printer.parseDeviceStatus(status)}')
        printer.disconnect()
        sys.exit(0)

    elif 'battery' in args and args.battery:

        try:
            battery = printer.getDeviceBattery()
            print(f'battery: {battery}%')
        except Exception as e:
            print(f'Failed to read battery: {e!r}')
            printer.disconnect()
            sys.exit(1)

        printer.disconnect()
        sys.exit(0)

    elif 'introduce' in args and args.introduce:

        # print('Hello, my name is Harold..')
        print(printer.getDeviceFull().decode('ascii'))
        printer.disconnect()
        sys.exit(0)

    elif 'stream' in args and args.stream:

        printer.setConcentration(args.concentration)
        apply_printer_options()

        while True:
            try:
                line = input().rstrip()

                printer.printlnASCII(line)

            except EOFError:
                # Input closed ^d^d
                break

        if args.break_size > 0:
            printer.printBreak(args.break_size)

        printer.disconnect()

        sys.exit(0)

    elif 'text' in args and args.text is not None:

        printer.setConcentration(args.concentration)
        apply_printer_options()

        text = args.text.rstrip()

        if len(text) > 0:
            printer.printASCII(text)
            printer.flushASCII()

        if args.break_size > 0:
            printer.printBreak(args.break_size)

        printer.disconnect()

        sys.exit(0)

    elif 'image' in args and args.image is not None:

        printer.setConcentration(args.concentration)
        apply_printer_options()

        try:
            img = PIL.Image.open(args.image)
        except:
            print(f'Failed to open image { args.image }')

        printer.printImage(img, compressed=args.compressed)

        if args.break_size > 0:
            printer.printBreak(args.break_size)

        printer.disconnect()

        sys.exit(0)

    elif 'qr' in args and args.qr is not None:

        printer.setConcentration(args.concentration)
        apply_printer_options()

        printer.printQR(args.qr, compressed=args.compressed)

        if args.break_size > 0:
            printer.printBreak(args.break_size)

        printer.disconnect()

        sys.exit(0)

    else:

        print('How did you get there?')

if __name__ == '__main__':
    main()
