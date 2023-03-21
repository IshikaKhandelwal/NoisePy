import argparse
import obspy
import os
import typing
from enum import Enum
from S0A_download_ASDF_MPI import download

# Utility running the different steps from the command line. Defines the arguments for each step

DATE_FORMAT = '%%Y_%%m_%%d_%%H_%%M_%%S'
default_start_date = "2016_07_01_0_0_0"                               # start date of download
default_end_date   = "2016_07_02_0_0_0"                               # end date of download

class Step(Enum):
    DOWNLOAD = 1
    CROSS_CORRELATE = 2
    STACK = 3

def valid_date(d: str) -> str:
    _ = obspy.UTCDateTime(d)
    return d    

def main(args: typing.Any):
    if args.step == Step.DOWNLOAD:
        download(args.path, args.channels, args.stations, [args.start], [args.end], args.inc_hours)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='step', required=True)
    
    # Download arguments
    down_parser = subparsers.add_parser(Step.DOWNLOAD.name.lower(), formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    down_parser.add_argument("--path", type=str, default=os.path.join(os.path.expanduser('~'), 'Documents/SCAL'), help="Directory for downloading files")
    down_parser.add_argument("--start", type=valid_date, required=True, help="Start date in the format: "+DATE_FORMAT, default=default_start_date)
    down_parser.add_argument("--end",   type=valid_date, required=True, help="End date in the format: "+DATE_FORMAT, default=default_end_date)
    down_parser.add_argument("--stations", type=lambda s: s.split(","), help="Comma separated list of stations or '*' for all", default="*")
    down_parser.add_argument("--channels", type=lambda s: s.split(","), help="Comma separated list of channels", default="BHE,BHN,BHZ")
    down_parser.add_argument("--inc_hours", type=int, default=24, help="Time increment size (hrs)")
    args = parser.parse_args()
    args.step = Step[args.step.upper()]
    main(args)

