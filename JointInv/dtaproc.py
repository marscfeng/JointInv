#!/usr/bin/env python
# -*- coding:utf8 -*-
"""
Script to earthquake data processing.

miniSEED should be organized as:

    |--- YYYY-MM
    |    |-- NET.STA.CHA.mseed
    |    |-- NET.STA.CHA.mseed
    |    `-- ...
    |--- YYYY-MM
    |    |-- NET.STA.CHA.mseed
    |    |-- NET.STA.CHA.mseed
    |    `-- ...
    |--- ...

For example:

    |--- 2016-01
    |    |-- NET1.STA1.BHE.mseed
    |    |-- NET1.STA1.BHN.mseed
    |    |-- NET1.STA1.BHZ.mseed
    |    `-- ...
    |--- 2016-01
    |    |-- NET1.STA1.BHE.mseed
    |    |-- NET1.STA1.BHN.mseed
    |    |-- NET1.STA1.BHZ.mseed
    |    `-- ...
    |--- ...

Output SAC files are organized as:

    |--- YYYYMMDDHHMMSS
    |    |-- YYYY.JDAY.HH.MM.SS.0000.NET.STA.LOC.CHA.M.SAC
    |    |-- YYYY.JDAY.HH.MM.SS.0000.NET.STA.LOC.CHA.M.SAC
    |    `-- ...
    |--- YYYYMMDDHHMMSS
    |    |-- YYYY.JDAY.HH.MM.SS.0000.NET.STA.LOC.CHA.M.SAC
    |    |-- YYYY.JDAY.HH.MM.SS.0000.NET.STA.LOC.CHA.M.SAC
    |    `-- ...
    |--- ...

For example:


    |--- 20160103230522
    |    |-- 2016.003.23.05.22.0000.AH.ANQ.00.BHE.M.mseed
    |    |-- 2016.003.23.05.22.0000.AH.ANQ.00.BHN.M.mseed
    |    |-- 2016.003.23.05.22.0000.AH.ANQ.00.BHZ.M.mseed
    |    `-- ...
    |--- 20160105140322
    |    |-- 2016.005.14.03.22.0000.AH.ANQ.00.BHE.M.mseed
    |    |-- 2016.005.14.03.22.0000.AH.ANQ.00.BHN.M.mseed
    |    |-- 2016.005.14.03.22.0000.AH.ANQ.00.BHZ.M.mseed
    |    `-- ...
"""
import os
import logging
from datetime import timedelta

from obspy import read, UTCDateTime, Stream
from obspy.io.sac import SACTrace
from obspy.taup import TauPyModel
from obspy.geodetics import locations2degrees
from obspy.geodetics.base import gps2dist_azimuth
# Setup the logger
FORMAT = "[%(asctime)s]  %(levelname)s: %(message)s"
logging.basicConfig(
    level=logging.DEBUG,
    format=FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class Client(object):
    def __init__(self, stationinfo, mseeddir, sacdir, model='prem'):
        self.mseeddir = mseeddir
        self.sacdir = sacdir
        self.stations = self._read_stations(stationinfo)
        self.model = TauPyModel(model=model)

    def _read_stations(self, stationinfo):
        """
        Read station information from station metadata file.

        Format of station information:

            NET.STA  latitude  longitude  elevation
        """
        stations = []
        with open(stationinfo, "r") as f:
            for line in f:
                name, stla, stlo, stel = line.split()[0:4]
                station = {"name": name,
                           "stla": float(stla),
                           "stlo": float(stlo),
                           "stel": float(stel)
                           }
                stations.append(station)
        logger.info("%d stations found.", len(stations))
        return stations

    def _get_dirname(self, starttime, endtime):
        """
        Get directory names based on starttime and endtime.
        """
        if not starttime and not endtime:
            return []
        if starttime.month == endtime.month:  # one day
            return [starttime.strftime("%Y-%m")]
        else:  # two days
            return [starttime.strftime("%Y-%m"),
                    endtime.strftime("%Y-%m")]

    def _read_mseed(self, station, dirnames, starttime, endtime):
        """
        Read waveform in specified time window.
        """
        # obtain event waveform
        pattern = station['name'] + ".*.mseed"
        if not 1 <= len(dirnames) <= 2:  # zero or more than two days
            logger.error("Cannot trim waveform duration span %s month(s)",
                         len(dirnames))
            return

        # loop over to read all mseed in
        st = Stream()
        for dirname in dirnames:
            mseedname = os.path.join(self.mseeddir, dirname, pattern)
            try:
                st += read(mseedname)
            except FileNotFoundError:
                logger.warning("File not exist: %s", mseedname)
            except Exception as e:
                logger.error("Error in reading: %s", e)

        # Merge data
        try:
            st.merge(fill_value=0)
        except Exception:
            logger.error("Error in merging %s", station['name'])
            return None

        # check if st contains data
        if not st:
            logger.warning("No data for %s", station['name'])
            return None

        st.trim(starttime, endtime)
        return st

    def _writesac(self, stream, event, station, outdir):
        """
        Write data with SAC format with event and station information.
        """
        for trace in stream:  # loop over 3-component traces
            # transfer obspy trace to sac trace
            sac_trace = SACTrace.from_obspy_trace(trace=trace)

            # set station related headers
            sac_trace.stla = station["stla"]
            sac_trace.stlo = station["stlo"]
            sac_trace.stel = station["stel"]

            if trace.stats.channel[-1] == "E":
                sac_trace.cmpaz = 90
                sac_trace.cmpinc = 90
            elif trace.stats.channel[-1] == "N":
                sac_trace.cmpaz = 0
                sac_trace.cmpinc = 90
            elif trace.stats.channel[-1] == "Z":
                sac_trace.cmpaz = 0
                sac_trace.cmpinc = 0
            else:
                logger.warning("Not E|N|Z component")

            # set event related headers
            sac_trace.evla = event["latitude"]
            sac_trace.evlo = event["longitude"]
            sac_trace.evdp = event["depth"]
            sac_trace.mag = event["magnitude"]

            # 1. SACTrace.from_obspy_trace automatically set Trace starttime
            #    as the reference time of SACTrace, when converting Trace to
            #    SACTrace. Thus in SACTrace, b = 0.0.
            # 2. Set SACTrace.o as the time difference in seconds between
            #    event origin time and reference time (a.k.a. starttime).
            # 3. Set SACTrace.iztype to 'io' change the reference time to
            #    event origin time (determined by SACTrace.o) and also
            #    automatically change other time-related headers
            #    (e.g. SACTrace.b).

            # 1.from_obspy_trace
            #   o
            #   |
            #   b----------------------e
            #   |=>   shift  <=|
            # reftime          |
            #               origin time
            #
            # 2.sac_trace.o = shift
            #   o:reset to be zero
            #   |
            #   b---------------------e
            #   |            |
            #   | refer(origin) time
            # -shift
            sac_trace.o = event["origin"] - sac_trace.reftime
            sac_trace.iztype = 'io'
            sac_trace.lcalda = True

            # SAC file location
            sac_flnm = ".".join([event["origin"].strftime("%Y.%j.%H.%M.%S"),
                                 "0000", trace.id, "M", "SAC"])
            sac_fullname = os.path.join(outdir, sac_flnm)
            sac_trace.write(sac_fullname)
        return

    def _get_window(self, event, station=None, by_event=None, by_phase=None,
                    by_speed=None):
        """
        Determine the starttime and endtime

        Parameters
        ----------

        event: dict
            Contain information of events
        station: dict
            Contain information of station
        """

        if by_event:
            starttime = event['origin'] + by_event['start_offset']
            endtime = starttime + by_event['duration']
            return starttime, endtime

        dist_km = gps2dist_azimuth(event["latitude"], event["longitude"],
                                   station["stla"], station["stlo"])[0] / 1000.0
        if by_speed:
            starttime = event['origin'] + dist_km / by_speed["maximum"]
            endtime = event['origin'] + dist_km / by_speed["minimum"]
            return starttime, endtime

        # by phase
        # calculate epicenter
        dist = locations2degrees(event["latitude"], event["longitude"],
                                 station["stla"], station["stlo"])
        start_ref_phase = by_phase['start_ref_phase']
        end_ref_phase = by_phase['end_ref_phase']
        start_offset = by_phase['start_offset']
        end_offset = by_phase['end_offset']


        # TauPyModel.get_travel_times always return sorted value
        start_arrivals = self.model.get_travel_times(
            source_depth_in_km=event['depth'],
            distance_in_degree=dist,
            phase_list=start_ref_phase)
        if not start_arrivals:  # no phase avaiable, skip this data
            return None, None  # starttime and endtime are None

        end_arrivals = self.model.get_travel_times(
            source_depth_in_km=event['depth'],
            distance_in_degree=dist,
            phase_list=end_ref_phase)

        # determine starttime and endtime
        starttime = event['origin'] + start_arrivals[0].time + start_offset
        endtime = event['origin'] + end_arrivals[-1].time + end_offset
        return starttime, endtime

    def get_waveform(self, event, by_event=None, by_phase=None,
                     by_speed=None, epicenter=None):
        """
        Trim waveform from dataset of CGRM

        Parameters
        ----------
        event: dict
            Event information container
        by_event: dict
            Determine waveform window by event origin time
        by_phase: dict
            Determine waveform window by phase arrival times
        by_speed: dict
            Determine waveform window by minimum and maximum speed [km/s]
        epicenter: dict
            Select station location [degree]
        """
        # check the destination
        eventdir = event['origin'].strftime("%Y%m%d%H%M%S")
        outdir = os.path.join(self.sacdir, eventdir)
        if not os.path.exists(outdir):
            os.makedirs(outdir, exist_ok=True)

        if by_event:
            starttime, endtime = self._get_window(event=event,
                                                  by_event=by_event)
            dirnames = self._get_dirname(starttime, endtime)
            logger.debug("dirnames: %s", dirnames)


        # loop over all stations
        for station in self.stations:
            logger.debug("station: %s", station['name'])
            if not by_event and not by_speed:
                starttime, endtime = self._get_window(event=event,
                                                      station=station,
                                                      by_phase=by_phase)
                dirnames = self._get_dirname(starttime, endtime)
                logger.debug("dirnames: %s", dirnames)
            if epicenter:
                dist = locations2degrees(event["latitude"], event["longitude"],
                                              station["stla"], station["stlo"])
                if dist < epicenter['minimum'] or dist > epicenter['maximum']:
                    continue
            if by_speed:
                starttime, endtime = self._get_window(event=event,
                                                      station=station,
                                                      by_speed=by_speed)
                dirnames = self._get_dirname(starttime, endtime)
            logger.debug("dirnames: %s", dirnames)

            st = self._read_mseed(station, dirnames, starttime, endtime)
            if not st:
                continue
            self._writesac(st, event, station, outdir)


            def _combine_stas(self):
                """
                Combine station and return station pairs

                Parameters
                ----------
                sta_pairs: list
                    station pairs container
                """
                sta_pairs = [{sta1.name:sta1,sta2.name:sta2}
                             for sta1 in self.stations
                             for sta2 in self.stations if sta2 != sta1]
                return sta_pairs

            def _linecriterion(self, sta_pair, event):
                """
                Judge if station pair share the same great circle with event.
                (angle between line connecting stations and events is less than
                three degrees)

                """

                # calculate azimuth and back-azimuth
                sta1, sta2 = sta_pair.values()
                intersta = DistAz(sta1["stla"], sta1["stlo"],
                                  sta2["stla"], sta2["stlo"])
                sta2evnt = DistAz(sta1["stla"], sta1["stlo"],
                                  event["latitude"], event["longitude"])

                # sta1-sta2-event
                if np.abs(intersta.az-sta2evnt.az) < 3:
                    logger.info("Common line: %s-%s-%s", sta1['name'],
                                sta2['name'], event['origin'] )
                    return {'sta1':sta1, 'sta2':sta2, 'event':event}# sta2-sta1-event
                elif np.abs(np.abs(intersta.az-sta2evnt.az)-180) < 3:
                    logger.info("Common line: %s-%s-%s", sta2['name'],
                                sta1['name'], event['origin'] )
                    return {'sta1':sta2, 'sta2':sta1, 'event':event}
                else:
                    return {}

            def comlineselector(self, event):
                """
                Select matched station pair and event

                """
                sta_pairs = self._combine_stas()
                matched_pair = []
                for sta_pair in sta_pairs:
                    selector = self._linecriterion(sta_pair, event)
                    if not selector:
                        continue
               matched_pair = [self._linecriterion(sta_pair, event)
                               for sta_pair in sta_pairs
                               if self._linecriterion(sta_pair, event)]
               return matched_pair

def read_catalog(catalog):
    '''
    Read event catalog.

    Format of event catalog:

        origin  latitude  longitude  depth  magnitude  magnitude_type

    Example:

        2016-01-03T23:05:22.270  24.8036   93.6505  55.0 6.7  mww
    '''
    events = []
    with open(catalog) as f:
        for line in f:
            origin, latitude, longitude, depth, magnitude = line.split()[0:5]
            event = {
                "origin": UTCDateTime(origin),
                "latitude": float(latitude),
                "longitude": float(longitude),
                "depth": float(depth),
                "magnitude": float(magnitude),
            }
            events.append(event)
    return events


if __name__ == '__main__':
    stationinfo="./TrimData/XJSTA.info"
    mseeddir="../../TianShan/MINISEEDData"
    sacdir="./TrimData/SAC"
    model="prem"
    client = Client(stationinfo=stationinfo,
                    mseeddir=mseeddir,
                    sacdir=sacdir,
                    model=model)

    events = read_catalog("./Catalog/catalog/catalog.csv")
    """
    # Data Trim part

    epicenter = {
        "minimum": 30,
        "maximum": 40
    }
    for event in events:
        logger.info("origin: %s", event['origin'])
        by_event = {"start_offset": 0, "duration": 6000}
        by_phase = {
            "start_ref_phase": ['P', 'p'],
            "start_offset": -100,
            "end_ref_phase": ['PcP'],
            "end_offset": 200
        }
        by_speed = {
                "minimum":2,
                "maximum":5
                }

        client.get_waveform(event, by_speed=by_speed)

    """
    for event in events:
        logger.info("origin: %s", event['origin'])
        eventdir = event['origin'].strftime("%Y%m%d%H%M%S")
        outdir = os.path.join(sacdir, eventdir)

        # check if directory and files inside exist
        if not os.path.exists(outdir) and not glob.glob(outdir+"/*"):
            logger.error("FileNotFoundError")
            continue




