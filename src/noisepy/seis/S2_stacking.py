import glob
import logging
import os
import sys
import time
from typing import List

import numpy as np
import pandas as pd
import pyasdf
from mpi4py import MPI

from noisepy.seis.datatypes import ConfigParameters

from . import noise_module

logger = logging.getLogger(__name__)
if not sys.warnoptions:
    import warnings

    warnings.simplefilter("ignore")

"""
Stacking script of NoisePy to:
    1) load cross-correlation data for sub-stacking (if needed) and all-time average;
    2) stack data with either linear or phase weighted stacking (pws) methods (or both);
    3) save outputs in ASDF or SAC format depend on user's choice (for latter option, find the script of write_sac
       in the folder of application_modules;
    4) rotate from a E-N-Z to R-T-Z system if needed.

Authors: Chengxin Jiang (chengxin_jiang@fas.harvard.edu)
         Marine Denolle (mdenolle@fas.harvard.edu)

NOTE:
    0. MOST occasions you just need to change parameters followed with detailed explanations to run the script.
    1. assuming 3 components are E-N-Z
    2. auto-correlation is not kept in the stacking due to the fact that it has only 6 cross-component.
    this tends to mess up the orders of matrix that stores the CCFs data
"""


# maximum memory allowed per core in GB
MAX_MEM = 4.0


# TODO: make stack_method an enum
def stack(sta: List[str], ccf_dir: str, stack_dir: str, fft_params: ConfigParameters):
    tt0 = time.time()
    if fft_params.rotation and fft_params.correction:
        corrfile = os.path.join(ccf_dir, "../meso_angles.txt")  # csv file containing angle info to be corrected
        locs = pd.read_csv(corrfile)
    else:
        locs = []

    # cross component info
    if fft_params.ncomp == 1:
        enz_system = ["ZZ"]
    else:
        enz_system = ["EE", "EN", "EZ", "NE", "NN", "NZ", "ZE", "ZN", "ZZ"]

    rtz_components = ["ZR", "ZT", "ZZ", "RR", "RT", "RZ", "TR", "TT", "TZ"]

    stack_metadata = os.path.join(stack_dir, "stack_data.txt")

    #######################################
    # #########PROCESSING SECTION##########
    #######################################

    # --------MPI---------
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    if rank == 0:
        if not os.path.isdir(stack_dir):
            os.mkdir(stack_dir)
        # save metadata
        fout = open(stack_metadata, "w")
        fout.write(str(fft_params))
        fout.close()

        # cross-correlation files
        ccfiles = sorted(glob.glob(os.path.join(ccf_dir, "*.h5")))
        logger.debug(ccfiles)

        for ii in range(len(sta)):
            tmp = os.path.join(stack_dir, sta[ii])
            if not os.path.isdir(tmp):
                os.mkdir(tmp)

        # station-pairs
        pairs_all = []
        for ii in range(len(sta) - 1):
            for jj in range(ii, len(sta)):
                pairs_all.append(sta[ii] + "_" + sta[jj])

        splits = len(pairs_all)
        if len(ccfiles) == 0 or splits == 0:
            raise IOError("Abort! no available CCF data for stacking")

    else:
        splits, ccfiles, pairs_all = [None for _ in range(3)]

    # broadcast the variables
    splits = comm.bcast(splits, root=0)
    ccfiles = comm.bcast(ccfiles, root=0)
    pairs_all = comm.bcast(pairs_all, root=0)

    # MPI loop: loop through each user-defined time chunck
    for ipair in range(rank, splits, size):
        t0 = time.time()

        logger.debug("%dth path for station-pair %s" % (ipair, pairs_all[ipair]))
        # source folder
        ttr = pairs_all[ipair].split("_")
        snet, ssta = ttr[0].split(".")
        rnet, rsta = ttr[1].split(".")
        idir = ttr[0]

        # check if it is auto-correlation
        if ssta == rsta and snet == rnet:
            fauto = 1
        else:
            fauto = 0

        # continue when file is done: TODO: Remove this and use a Store.contains() function.
        toutfn = os.path.join(stack_dir, idir + "/" + pairs_all[ipair] + ".tmp")
        if os.path.isfile(toutfn):
            continue

        # crude estimation on memory needs (assume float32)
        nccomp = fft_params.ncomp * fft_params.ncomp
        num_chunck = len(ccfiles) * nccomp
        num_segmts = 1
        if fft_params.substack:  # things are difference when do substack
            if fft_params.substack_len == fft_params.cc_len:
                num_segmts = int(np.floor((fft_params.inc_hours * 3600 - fft_params.cc_len) / fft_params.step))
            else:
                num_segmts = int(fft_params.inc_hours / (fft_params.substack_len / 3600))
        npts_segmt = int(2 * fft_params.maxlag * fft_params.samp_freq) + 1
        memory_size = num_chunck * num_segmts * npts_segmt * 4 / 1024**3

        if memory_size > MAX_MEM:
            raise ValueError(
                "Require %5.3fG memory but only %5.3fG provided)! Cannot load cc data all once!"
                % (memory_size, MAX_MEM)
            )
        logger.debug("Good on memory (need %5.2f G and %s G provided)!" % (memory_size, MAX_MEM))
        # allocate array to store fft data/info
        cc_array = np.zeros((num_chunck * num_segmts, npts_segmt), dtype=np.float32)
        cc_time = np.zeros(num_chunck * num_segmts, dtype=np.float32)
        cc_ngood = np.zeros(num_chunck * num_segmts, dtype=np.int16)
        cc_comp = np.chararray(num_chunck * num_segmts, itemsize=2, unicode=True)

        # loop through all time-chuncks
        iseg = 0
        dtype = pairs_all[ipair]
        for ifile in ccfiles:
            # load the data from daily compilation
            ds = pyasdf.ASDFDataSet(ifile, mpi=False, mode="r")
            try:
                path_list = ds.auxiliary_data[dtype].list()
                tparameters = ds.auxiliary_data[dtype][path_list[0]].parameters
            except Exception:
                logger.debug("continue! no pair of %s in %s" % (dtype, ifile))
                continue
            logger.debug(f"path_list for {dtype}: {path_list}")
            # seperate auto and cross-correlation
            if fauto == 1:
                if fft_params.ncomp == 3 and len(path_list) < 6:
                    logger.warning(
                        "continue! not enough cross components for auto-correlation %s in %s" % (dtype, ifile)
                    )
                    continue
            else:
                if fft_params.ncomp == 3 and len(path_list) < 9:
                    logger.warning(
                        "continue! not enough cross components for cross-correlation %s in %s" % (dtype, ifile)
                    )
                    continue

            if len(path_list) > 9:
                raise ValueError("more than 9 cross-component exists for %s %s! please double check" % (ifile, dtype))

            # load the 9-component data, which is in order in the ASDF
            for tpath in path_list:
                cmp1 = tpath.split("_")[0]
                cmp2 = tpath.split("_")[1]
                tcmp1 = cmp1[-1]
                tcmp2 = cmp2[-1]

                # read data and parameter matrix
                tdata = ds.auxiliary_data[dtype][tpath].data[:]
                ttime = ds.auxiliary_data[dtype][tpath].parameters["time"]
                tgood = ds.auxiliary_data[dtype][tpath].parameters["ngood"]
                if fft_params.substack:
                    for ii in range(tdata.shape[0]):
                        cc_array[iseg] = tdata[ii]
                        cc_time[iseg] = ttime[ii]
                        cc_ngood[iseg] = tgood[ii]
                        cc_comp[iseg] = tcmp1 + tcmp2
                        iseg += 1
                else:
                    cc_array[iseg] = tdata
                    cc_time[iseg] = ttime
                    cc_ngood[iseg] = tgood
                    cc_comp[iseg] = tcmp1 + tcmp2
                    iseg += 1

        t1 = time.time()
        logger.debug("loading CCF data takes %6.2fs" % (t1 - t0))

        # continue when there is no data or for auto-correlation
        if iseg <= 1 and fauto == 1:
            continue
        outfn = pairs_all[ipair] + ".h5"
        logger.debug("ready to output to %s" % (outfn))

        # matrix used for rotation
        if fft_params.rotation:
            bigstack = np.zeros(shape=(9, npts_segmt), dtype=np.float32)
        if fft_params.stack_method == "all":
            bigstack1 = np.zeros(shape=(9, npts_segmt), dtype=np.float32)
            bigstack2 = np.zeros(shape=(9, npts_segmt), dtype=np.float32)

        # loop through cross-component for stacking
        iflag = 1
        for icomp in range(nccomp):
            comp = enz_system[icomp]
            indx = np.where(cc_comp.lower() == comp.lower())[0]
            logger.debug(f"index to find the comp: {indx}")

            # jump if there are not enough data
            if len(indx) < 2:
                iflag = 0
                break

            stack_h5 = os.path.join(stack_dir, idir + "/" + outfn)
            logger.debug(f"h5 stack path: {stack_h5}")
            # output stacked data
            (
                cc_final,
                ngood_final,
                stamps_final,
                allstacks1,
                allstacks2,
                allstacks3,
                nstacks,
            ) = noise_module.stacking(cc_array[indx], cc_time[indx], cc_ngood[indx], fft_params)
            logger.debug(f"after stacking nstacks: {nstacks}")
            if not len(allstacks1):
                continue
            if fft_params.rotation:
                bigstack[icomp] = allstacks1
                if fft_params.stack_method == "all":
                    bigstack1[icomp] = allstacks2
                    bigstack2[icomp] = allstacks3

            # write stacked data into ASDF file
            with pyasdf.ASDFDataSet(stack_h5, mpi=False) as ds:
                tparameters["time"] = stamps_final[0]
                tparameters["ngood"] = nstacks
                if fft_params.stack_method != "all":
                    data_type = "Allstack_" + fft_params.stack_method
                    ds.add_auxiliary_data(
                        data=allstacks1,
                        data_type=data_type,
                        path=comp,
                        parameters=tparameters,
                    )
                else:
                    ds.add_auxiliary_data(
                        data=allstacks1,
                        data_type="Allstack_linear",
                        path=comp,
                        parameters=tparameters,
                    )
                    ds.add_auxiliary_data(
                        data=allstacks2,
                        data_type="Allstack_pws",
                        path=comp,
                        parameters=tparameters,
                    )
                    ds.add_auxiliary_data(
                        data=allstacks3,
                        data_type="Allstack_robust",
                        path=comp,
                        parameters=tparameters,
                    )
            # keep a track of all sub-stacked data from S1
            if fft_params.keep_substack:
                for ii in range(cc_final.shape[0]):
                    with pyasdf.ASDFDataSet(stack_h5, mpi=False) as ds:
                        tparameters["time"] = stamps_final[ii]
                        tparameters["ngood"] = ngood_final[ii]
                        data_type = "T" + str(int(stamps_final[ii]))
                        ds.add_auxiliary_data(
                            data=cc_final[ii],
                            data_type=data_type,
                            path=comp,
                            parameters=tparameters,
                        )

            t3 = time.time()
            logger.debug(
                "takes %6.2fs to stack one component with %s stacking method" % (t3 - t1, fft_params.stack_method)
            )

        # do rotation if needed
        if fft_params.rotation and iflag:
            if np.all(bigstack == 0):
                continue
            tparameters["station_source"] = ssta
            tparameters["station_receiver"] = rsta
            if fft_params.stack_method != "all":
                bigstack_rotated = noise_module.rotation(bigstack, tparameters, locs)

                # write to file
                for icomp in range(nccomp):
                    comp = rtz_components[icomp]
                    tparameters["time"] = stamps_final[0]
                    tparameters["ngood"] = nstacks
                    data_type = "Allstack_" + fft_params.stack_method
                    with pyasdf.ASDFDataSet(stack_h5, mpi=False) as ds2:
                        ds2.add_auxiliary_data(
                            data=bigstack_rotated[icomp],
                            data_type=data_type,
                            path=comp,
                            parameters=tparameters,
                        )
            else:
                bigstack_rotated = noise_module.rotation(bigstack, tparameters, locs)
                bigstack_rotated1 = noise_module.rotation(bigstack1, tparameters, locs)
                bigstack_rotated2 = noise_module.rotation(bigstack2, tparameters, locs)

                # write to file
                for icomp in range(nccomp):
                    comp = rtz_components[icomp]
                    tparameters["time"] = stamps_final[0]
                    tparameters["ngood"] = nstacks
                    with pyasdf.ASDFDataSet(stack_h5, mpi=False) as ds2:
                        ds2.add_auxiliary_data(
                            data=bigstack_rotated[icomp],
                            data_type="Allstack_linear",
                            path=comp,
                            parameters=tparameters,
                        )
                        ds2.add_auxiliary_data(
                            data=bigstack_rotated1[icomp],
                            data_type="Allstack_pws",
                            path=comp,
                            parameters=tparameters,
                        )
                        ds2.add_auxiliary_data(
                            data=bigstack_rotated2[icomp],
                            data_type="Allstack_robust",
                            path=comp,
                            parameters=tparameters,
                        )

        t4 = time.time()
        logger.debug("takes %6.2fs to stack/rotate all station pairs %s" % (t4 - t1, pairs_all[ipair]))

        # write file stamps
        ftmp = open(toutfn, "w")
        ftmp.write("done")
        ftmp.close()

    tt1 = time.time()
    logger.info("it takes %6.2fs to process step 2 in total" % (tt1 - tt0))
    comm.barrier()


# Point people to new entry point:
if __name__ == "__main__":
    print("Please see:\n\npython noisepy.py stack --help\n")
