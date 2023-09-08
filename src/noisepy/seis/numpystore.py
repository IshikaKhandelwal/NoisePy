import io
import json
import logging
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from datetimerange import DateTimeRange

from noisepy.seis.constants import DONE_PATH
from noisepy.seis.datatypes import Station
from noisepy.seis.hierarchicalstores import (
    ArrayStore,
    HierarchicalCCStoreBase,
    HierarchicalStackStoreBase,
)
from noisepy.seis.stores import parse_station_pair, parse_timespan
from noisepy.seis.utils import fs_join, get_filesystem

logger = logging.getLogger(__name__)

TAR_GZ_EXTENSION = ".tar.gz"
FILE_ARRAY_NPY = "array.npy"
FILE_PARAMS_JSON = "params.json"


class NumpyArrayStore(ArrayStore):
    def __init__(self, root_dir: str, mode: str, storage_options={}) -> None:
        super().__init__()
        logger.info(f"store creating at {root_dir}, mode={mode}, storage_options={storage_options}")
        storage_options["client_kwargs"] = {"region_name": "us-west-2"}
        self.root_path = root_dir
        self.storage_options = storage_options
        self.fs = get_filesystem(root_dir, storage_options=storage_options)
        logger.info(f"Numpy store created at {root_dir}")

    def load_paths(self) -> List[str]:
        return self.fs.find(self.root_path)

    def append(self, path: str, params: Dict[str, Any], data: np.ndarray):
        logger.debug(f"Appending to {path}: {data.shape}")

        js = json.dumps(params)

        def add_file_bytes(tar, name, f):
            f.seek(0)
            ti = tarfile.TarInfo(name=name)
            ti.size = f.getbuffer().nbytes
            tar.addfile(ti, fileobj=f)

        self.fs.makedirs(fs_join(self.root_path, path), exist_ok=True)
        with self.fs.open(fs_join(self.root_path, path + TAR_GZ_EXTENSION), "wb") as f:
            with tarfile.open(fileobj=f, mode="w:gz") as tar:
                with io.BytesIO() as npyf:
                    np.save(npyf, data, allow_pickle=False)
                    with io.BytesIO() as jsf:
                        jsf.write(js.encode("utf-8"))

                        add_file_bytes(tar, FILE_ARRAY_NPY, npyf)
                        add_file_bytes(tar, FILE_PARAMS_JSON, jsf)

    def is_done(self, key: str):
        if DONE_PATH not in self.root.array_keys():
            return False
        done_array = self.root[DONE_PATH]
        return key in done_array.attrs.keys()

    def mark_done(self, key: str):
        done_array = self.root.require_dataset(DONE_PATH, shape=(1, 1))
        done_array.attrs[key] = True

    def read(self, path: str) -> Optional[Tuple[np.ndarray, Dict[str, Any]]]:
        file = fs_join(self.root_path, path + TAR_GZ_EXTENSION)
        if not self.fs.exists(file):
            return None

        with self.fs.open(file, "rb") as f:
            with tarfile.open(fileobj=f, mode="r:gz") as tar:
                npy_mem = tar.getmember(FILE_ARRAY_NPY)
                with tar.extractfile(npy_mem) as f:
                    array_file = io.BytesIO()
                    array_file.write(f.read())
                    array_file.seek(0)
                    array = np.load(array_file, allow_pickle=False)
                params_mem = tar.getmember(FILE_PARAMS_JSON)
                with tar.extractfile(params_mem) as f:
                    params = json.load(f)
                return (array, params)


class NumpyCCStore(HierarchicalCCStoreBase):
    def __init__(self, root_dir: str, mode: str = "a", storage_options={}) -> None:
        super().__init__(NumpyArrayStore(root_dir, mode, storage_options=storage_options))

    def get_timespans(self) -> List[DateTimeRange]:
        paths = list(filter(lambda x: x.endswith(TAR_GZ_EXTENSION), self.helper.load_paths()))
        timespans = set(Path(p).name.removesuffix(TAR_GZ_EXTENSION) for p in paths)
        timespans.discard(None)
        return list(map(parse_timespan, sorted(timespans)))

    def get_station_pairs(self) -> List[Tuple[Station, Station]]:
        paths = self.helper.load_paths()
        paths = set(Path(p).parts[-2] for p in paths)

        pairs = [parse_station_pair(k) for k in paths]
        return [p for p in pairs if p]


class NumpyStackStore(HierarchicalStackStoreBase):
    def __init__(self, root_dir: str, mode: str = "a", storage_options={}) -> None:
        super().__init__(NumpyArrayStore(root_dir, mode, storage_options=storage_options))
