import warnings
import numpy as np
from datetime import datetime, timedelta

from DHI.Generic.MikeZero import eumUnit, eumQuantity
from DHI.Generic.MikeZero.DFS import (
    DfsFileFactory,
    DfsFactory,
    DfsSimpleType,
    DataValueType,
)
from DHI.Generic.MikeZero.DFS.dfs123 import Dfs1Builder

from .dutil import Dataset, find_item, get_item_info, get_valid_items_and_timesteps
from .dotnet import (
    to_numpy,
    to_dotnet_float_array,
    to_dotnet_datetime,
    from_dotnet_datetime,
)
from .eum import TimeStep, ItemInfo
from .helpers import safe_length
from .dfs import Dfs123


class Dfs1(Dfs123):

    _dx = None

    def __init__(self, filename=None):
        super(Dfs1, self).__init__(filename)

        if filename:
            self._read_dfs1_header()
            

    def _read_dfs1_header(self):
        dfs = DfsFileFactory.Dfs1FileOpen(self._filename)
        self._dx = dfs.SpatialAxis.Dx

        self._read_header(dfs)


    def read(self, items=None, time_steps=None):
        """
        Read data from a dfs1 file
        
        Parameters
        ---------
        filename: str
            dfs2 filename
        items: list[int] or list[str], optional
            Read only selected items, by number (0-based), or by name
        time_steps: int or list[int], optional
            Read only selected time_steps

        Return:
            Dataset(data, time, items)
            where data[nt,x]
        """

        # NOTE. Item numbers are base 0 (everything else in the dfs is base 0)

        # Open the dfs file for reading
        dfs = DfsFileFactory.DfsGenericOpen(self._filename)
        self._dfs = dfs
        self._source = dfs

        nt = dfs.FileInfo.TimeAxis.NumberOfTimeSteps

        items, item_numbers, time_steps = get_valid_items_and_timesteps(self, items, time_steps)

        # Determine the size of the grid
        axis = dfs.ItemInfo[0].SpatialAxis

        xNum = axis.XCount
        nt = dfs.FileInfo.TimeAxis.NumberOfTimeSteps
        if nt == 0:
            raise Warning("Static dfs1 files (with no time steps) are not supported.")
            nt = 1
        deleteValue = dfs.FileInfo.DeleteValueFloat

        n_items = len(item_numbers)
        data_list = []

        for item in range(n_items):
            # Initialize an empty data block
            data = np.ndarray(shape=(len(time_steps), xNum), dtype=float)
            data_list.append(data)

        t_seconds = np.zeros(len(time_steps), dtype=float)

        for i in range(len(time_steps)):
            it = time_steps[i]
            for item in range(n_items):

                itemdata = dfs.ReadItemTimeStep(item_numbers[item] + 1, it)

                src = itemdata.Data
                d = to_numpy(src)

                d[d == deleteValue] = np.nan
                data_list[item][it, :] = d

            t_seconds[it] = itemdata.Time

        start_time = from_dotnet_datetime(dfs.FileInfo.TimeAxis.StartDateTime)
        time = [start_time + timedelta(seconds=tsec) for tsec in t_seconds]

        items = get_item_info(dfs, item_numbers)

        dfs.Close()
        return Dataset(data_list, time, items)

    def write(
        self,
        filename,
        data,
        start_time=None,
        dt=1,
        items=None,
        dx=1,
        x0=0,
        coordinate=None,
        timeseries_unit=TimeStep.SECOND,
        title=None,
    ):
        """
        Write a dfs1 file

        Parameters
        ----------
        filename: str
            Location to write the dfs1 file
        data: list[np.array]
            list of matrices, one for each item. Matrix dimension: x, time
        start_time: datetime, optional
            start datetime
        timeseries_unit: Timestep, optional
            TimeStep unit default TimeStep.SECOND
        dt: float
            The time step (double based on the timeseries_unit). Therefore dt of 5.5 with timeseries_unit of minutes
            means 5 mins and 30 seconds.
        items: list[ItemInfo], optional
            List of ItemInfo corresponding to a variable types (ie. Water Level).
        coordinate:
            ['UTM-33', 12.4387, 55.2257, 327]  for UTM, Long, Lat, North to Y orientation. Note: long, lat in decimal degrees
            OR
            [TODO: Support not Local Coordinates ...]
        x0:
            Lower right position
        dx:
            length of each grid in the x direction (meters)
        title:
            title of the dfs2 file (can be blank)

        """

        if title is None:
            title = ""

        n_time_steps = np.shape(data[0])[0]
        number_x = np.shape(data[0])[1]
        n_items = len(data)

        if start_time is None:
            start_time = datetime.now()

        if coordinate is None:
            if self._projstr is not None:
                coordinate = [
                    self._projstr,
                    self._longitude,
                    self._latitude,
                    self._orientation,
                ]
            else:
                warnings.warn("No coordinate system provided")
                coordinate = ["LONG/LAT", 0, 0, 0]

        if dx is None:
            if self._dx is not None:
                dx = self._dx
            else:
                dx = 1

        if isinstance(data, Dataset):
            items = data.items
            start_time = data.time[0]
            if dt is None and len(data.time) > 1:
                if not data.is_equidistant:
                    raise Exception("Data is not equidistant in time.")
                dt = (data.time[1] - data.time[0]).total_seconds()
            data = data.data

        if items is None:
            items = [ItemInfo(f"temItem {i+1}") for i in range(n_items)]

        if not all(np.shape(d)[0] == n_time_steps for d in data):
            raise Warning(
                "ERROR data matrices in the time dimension do not all match in the data list. "
                "Data is list of matices [t, x]"
            )
        if not all(np.shape(d)[1] == number_x for d in data):
            raise Warning(
                "ERROR data matrices in the X dimension do not all match in the data list. "
                "Data is list of matices [t, x]"
            )

        if len(items) != n_items:
            raise Warning(
                "names must be an array of strings with the same number as matrices in data list"
            )

        #if not type(start_time) is datetime:
        #    raise Warning("start_time must be of type datetime ")

        system_start_time = to_dotnet_datetime(start_time)

        # Create an empty dfs1 file object
        factory = DfsFactory()
        builder = Dfs1Builder.Create(title, "mikeio", 0)

        # Set up the header
        builder.SetDataType(0)
        builder.SetGeographicalProjection(
            factory.CreateProjectionGeoOrigin(
                coordinate[0], coordinate[1], coordinate[2], coordinate[3]
            )
        )
        builder.SetTemporalAxis(
            factory.CreateTemporalEqCalendarAxis(
                timeseries_unit, system_start_time, 0, dt
            )
        )
        builder.SetSpatialAxis(
            factory.CreateAxisEqD1(eumUnit.eumUmeter, number_x, x0, dx)
        )

        for i in range(n_items):
            builder.AddDynamicItem(
                items[i].name,
                eumQuantity.Create(items[i].type, items[i].unit),
                DfsSimpleType.Float,
                DataValueType.Instantaneous,
            )

        try:
            builder.CreateFile(filename)
        except IOError:
            print("cannot create dfs2 file: ", filename)

        dfs = builder.GetFile()
        deletevalue = dfs.FileInfo.DeleteValueFloat  # -1.0000000031710769e-30

        for i in range(n_time_steps):
            for item in range(n_items):
                d = data[item][i, :]
                d[np.isnan(d)] = deletevalue

                darray = to_dotnet_float_array(d)
                dfs.WriteItemTimeStepNext(0, darray)

        dfs.Close()
