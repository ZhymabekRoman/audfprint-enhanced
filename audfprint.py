"""
audfprint.py

Implementation of acoustic-landmark-based robust fingerprinting.
Port of the Matlab implementation.

2014-05-25 Dan Ellis dpwe@ee.columbia.edu
"""
from loguru import logger

# For reporting progress time
import time
# For command line interface
import click
import os
# For __main__
import sys
# For multiprocessing options
import multiprocessing
import joblib

# The actual analyzer class/code
import audfprint_analyze
# Access to match functions, used in command line interface
import audfprint_match
# My hash_table implementation
import hash_table

time_clock = time.process_time


def filename_list_iterator(filelist, wavdir, wavext, listflag):
    """ Iterator to yeild all the filenames, possibly interpreting them
        as list files, prepending wavdir """
    if not listflag:
        for filename in filelist:
            yield os.path.join(wavdir, filename + wavext)
    else:
        for listfilename in filelist:
            with open(listfilename, 'r') as f:
                for filename in f:
                    yield os.path.join(wavdir, filename.rstrip('\n') + wavext)


# for saving precomputed fprints
def ensure_dir(dirname):
    """ ensure that the named directory exists """
    if len(dirname):
        if not os.path.exists(dirname):
            # There's a race condition for multiprocessor; don't worry if the
            # directory gets created before we get to it.
            try:
                os.makedirs(dirname)
            except:
                pass


# Command line interface

# basic operations, each in a separate function

def file_precompute_peaks_or_hashes(analyzer, filename, precompdir,
                                    precompext=None, hashes_not_peaks=True,
                                    skip_existing=False,
                                    strip_prefix=None):
    """ Perform precompute action for one file, return list
        of message strings """
    # If strip_prefix is specified and matches the start of filename,
    # remove it from filename.
    if strip_prefix and filename[:len(strip_prefix)] == strip_prefix:
        tail_filename = filename[len(strip_prefix):]
    else:
        tail_filename = filename
    # Form the output filename to check if it exists.
    # strip relative directory components from file name
    # Also remove leading absolute path (comp == '')
    relname = '/'.join([comp for comp in tail_filename.split('/')
                        if comp != '.' and comp != '..' and comp != ''])
    root = os.path.splitext(relname)[0]
    if precompext is None:
        if hashes_not_peaks:
            precompext = audfprint_analyze.PRECOMPEXT
        else:
            precompext = audfprint_analyze.PRECOMPPKEXT
    opfname = os.path.join(precompdir, root + precompext)
    if skip_existing and os.path.isfile(opfname):
        return ["file " + opfname + " exists (and --skip-existing); skipping"]
    else:
        # Do the analysis
        if hashes_not_peaks:
            type = "hashes"
            saver = audfprint_analyze.hashes_save
            output = analyzer.wavfile2hashes(filename)
        else:
            type = "peaks"
            saver = audfprint_analyze.peaks_save
            output = analyzer.wavfile2peaks(filename)
        # save the hashes or peaks file
        if len(output) == 0:
            message = "Zero length analysis for " + filename + " -- not saving."
        else:
            # Make sure the output directory exists
            ensure_dir(os.path.split(opfname)[0])
            # Write the file
            saver(opfname, output)
            message = ("wrote " + opfname + " ( %d %s, %.3f sec)"
                       % (len(output), type, analyzer.soundfiledur))
        return [message]


def file_precompute(analyzer, filename, precompdir, type='peaks', skip_existing=False, strip_prefix=None):
    """ Perform precompute action for one file, return list
        of message strings """
    logger.debug(time.ctime(), "precomputing", type, "for", filename, "...")
    hashes_not_peaks = (type == 'hashes')
    return file_precompute_peaks_or_hashes(analyzer, filename, precompdir,
                                           hashes_not_peaks=hashes_not_peaks,
                                           skip_existing=skip_existing,
                                           strip_prefix=strip_prefix)


def make_ht_from_list(analyzer, filelist, hashbits, depth, maxtime, pipe=None):
    """ Populate a hash table from a list, used as target for
        multiprocess division.  pipe is a pipe over which to push back
        the result, else return it """
    # Create new ht instance
    ht = hash_table.HashTable(hashbits=hashbits, depth=depth, maxtime=maxtime)
    # Add in the files
    for filename in filelist:
        hashes = analyzer.wavfile2hashes(filename)
        ht.store(filename, hashes)
    # Pass back to caller
    if pipe:
        pipe.send(ht)
    else:
        return ht


def do_cmd(cmd, analyzer, hash_tab, filename_iter, matcher, outdir, type, skip_existing=False, strip_prefix=None):
    """ Breaks out the core part of running the command.
        This is just the single-core versions.
    """
    if cmd == 'merge' or cmd == 'newmerge':
        # files are other hash tables, merge them in
        for filename in filename_iter:
            hash_tab2 = hash_table.HashTable(filename)
            if "samplerate" in hash_tab.params:
                assert hash_tab.params["samplerate"] == hash_tab2.params["samplerate"]
            else:
                # "newmerge" fails to setup the samplerate param
                hash_tab.params["samplerate"] = hash_tab2.params["samplerate"]
            hash_tab.merge(hash_tab2)

    elif cmd == 'precompute':
        # just precompute fingerprints, single core
        for filename in filename_iter:
            logger.trace(file_precompute(analyzer, filename, outdir, type, skip_existing=skip_existing, strip_prefix=strip_prefix))

    elif cmd == 'match':
        # Running query, single-core mode
        for num, filename in enumerate(filename_iter):
            msgs = matcher.file_match_to_msgs(analyzer, hash_tab, filename, num)
            logger.trace(msgs)

    elif cmd == 'new' or cmd == 'add':
        # Adding files
        tothashes = 0
        ix = 0
        for filename in filename_iter:
            logger.trace([time.ctime() + " ingesting #" + str(ix) + ": "
                    + filename + " ..."])
            dur, nhash = analyzer.ingest(hash_tab, filename)
            tothashes += nhash
            ix += 1

        logger.trace(["Added " + str(tothashes) + " hashes "
                + "(%.1f" % (tothashes / float(analyzer.soundfiletotaldur))
                + " hashes/sec)"])
    elif cmd == 'remove':
        # Removing files from hash table.
        for filename in filename_iter:
            hash_tab.remove(filename)

    elif cmd == 'list':
        hash_tab.list(lambda x: logger.trace([x]))

    else:
        raise ValueError("unrecognized command: " + cmd)


def multiproc_add(analyzer, hash_tab, filename_iter, report, ncores):
    """Run multiple threads adding new files to hash table"""
    # run ncores in parallel to add new files to existing HASH_TABLE
    # lists store per-process parameters
    # Pipes to transfer results
    rx = [[] for _ in range(ncores)]
    tx = [[] for _ in range(ncores)]
    # Process objects
    pr = [[] for _ in range(ncores)]
    # Lists of the distinct files
    filelists = [[] for _ in range(ncores)]
    # unpack all the files into ncores lists
    ix = 0
    for filename in filename_iter:
        filelists[ix % ncores].append(filename)
        ix += 1
    # Launch each of the individual processes
    for ix in range(ncores):
        rx[ix], tx[ix] = multiprocessing.Pipe(False)
        pr[ix] = multiprocessing.Process(target=make_ht_from_list,
                                         args=(analyzer, filelists[ix],
                                               hash_tab.hashbits,
                                               hash_tab.depth,
                                               (1 << hash_tab.maxtimebits),
                                               tx[ix]))
        pr[ix].start()
    # gather results when they all finish
    for core in range(ncores):
        # thread passes back serialized hash table structure
        hash_tabx = rx[core].recv()
        logger.trace(["hash_table " + str(core) + " has "
                + str(len(hash_tabx.names))
                + " files " + str(sum(hash_tabx.counts)) + " hashes"])
        # merge in all the new items, hash entries
        hash_tab.merge(hash_tabx)
        # finish that thread...
        pr[core].join()


def matcher_file_match_to_msgs(matcher, analyzer, hash_tab, filename):
    """Cover for matcher.file_match_to_msgs so it can be passed to joblib"""
    return matcher.file_match_to_msgs(analyzer, hash_tab, filename)


def do_cmd_multiproc(cmd, analyzer, hash_tab, filename_iter, matcher,
                     outdir, type, report, skip_existing=False,
                     strip_prefix=None, ncores=1):
    """ Run the actual command, using multiple processors """
    if cmd == 'precompute':
        # precompute fingerprints with joblib
        msgslist = joblib.Parallel(n_jobs=ncores)(
                joblib.delayed(file_precompute)(analyzer, file, outdir, type, skip_existing, strip_prefix=strip_prefix)
                for file in filename_iter
        )
        # Collapse into a single list of messages
        for msgs in msgslist:
            logger.trace(msgs)

    elif cmd == 'match':
        # Running queries in parallel
        msgslist = joblib.Parallel(n_jobs=ncores)(
                # Would use matcher.file_match_to_msgs(), but you
                # can't use joblib on an instance method
                joblib.delayed(matcher_file_match_to_msgs)(matcher, analyzer,
                                                           hash_tab, filename)
                for filename in filename_iter
        )
        for msgs in msgslist:
            logger.trace(msgs)

    elif cmd == 'new' or cmd == 'add':
        # We add by forking multiple parallel threads each running
        # analyzers over different subsets of the file list
        multiproc_add(analyzer, hash_tab, filename_iter, report, ncores)

    else:
        # This is not a multiproc command
        raise ValueError("unrecognized multiproc command: " + cmd)


# Command to separate out setting of analyzer parameters
def setup_analyzer(density, is_match, pks_per_frame, fanout, freq_sd, shifts, samplerate, continue_on_error):
    """Create a new analyzer object, taking values from docopts args"""
    # Create analyzer object; parameters will get set below
    analyzer = audfprint_analyze.Analyzer()
    # Read parameters from command line/docopts
    analyzer.density = float(density)
    analyzer.maxpksperframe = int(pks_per_frame)
    analyzer.maxpairsperpeak = int(fanout)
    analyzer.f_sd = float(freq_sd)
    analyzer.shifts = int(shifts)
    # fixed - 512 pt FFT with 256 pt hop at 11025 Hz
    analyzer.target_sr = int(samplerate)
    analyzer.n_fft = 512
    analyzer.n_hop = analyzer.n_fft // 2
    # set default value for shifts depending on mode
    if analyzer.shifts == 0:
        # Default shift is 4 for match, otherwise 1
        analyzer.shifts = 4 if is_match else 1
    analyzer.fail_on_error = not continue_on_error
    return analyzer


def setup_matcher(match_win, search_depth, min_count, max_matches, exact_count, find_time_range, time_quantile, sortbytime, illustrate, illustrate_hpf):
    """Create a new matcher objects, set parameters from docopt structure"""
    matcher = audfprint_match.Matcher()
    matcher.window = int(match_win)
    matcher.threshcount = int(min_count)
    matcher.max_returns = int(max_matches)
    matcher.search_depth = int(search_depth)
    matcher.sort_by_time = sortbytime
    matcher.exact_count = exact_count | illustrate | illustrate_hpf
    matcher.illustrate = illustrate | illustrate_hpf
    matcher.illustrate_hpf = illustrate_hpf
    matcher.find_time_range = find_time_range
    matcher.time_quantile = float(time_quantile)
    return matcher


__version__ = 20150406


@click.command(help="Landmark-based audio fingerprinting. Create a new fingerprint dbase with 'new', append new files to an existing database with 'add', or identify noisy query excerpts with 'match'. 'precompute' writes a *.fpt file under precompdir with precomputed fingerprint for each input wav file. 'merge' combines previously-created databases into an existing database; 'newmerge' combines existing databases to create a new one.", context_settings={'show_default': True})
@click.argument('cmd', type=click.Choice(['new', 'add', 'precompute', 'merge', 'newmerge', 'match', 'list', 'remove']))
@click.option('-d', '--dbase', help='Fingerprint database file')
@click.option('-n', '--density', default=20.0, help='Target hashes per second')
@click.option('-h', '--hashbits', default=20, help='How many bits in each hash')
@click.option('-b', '--bucketsize', default=100, help='Number of entries per bucket')
@click.option('-t', '--maxtime', default=16384, help='Largest time value stored')
@click.option('-u', '--maxtimebits', help='maxtime as a number of bits (16384 == 14 bits)')
@click.option('-r', '--samplerate', default=11025, help='Resample input files to this')
@click.option('-p', '--precompdir', default='.', help='Save precomputed files under this dir')
@click.option('-i', '--shifts', default=0, help='Use this many subframe shifts building fp')
@click.option('-w', '--match-win', default=2, help='Maximum tolerable frame skew to count as a match')
@click.option('-N', '--min-count', default=5, help='Minimum number of matching landmarks to count as a match')
@click.option('-x', '--max-matches', default=1, help='Maximum number of matches to report for each query')
@click.option('-X', '--exact-count', is_flag=True, help='Flag to use more precise (but slower) match counting')
@click.option('-R', '--find-time-range', is_flag=True, help='Report the time support of each match')
@click.option('-Q', '--time-quantile', default=0.05, help='Quantile at extremes of time support')
@click.option('-S', '--freq-sd', default=30.0, help='Frequency peak spreading SD in bins')
@click.option('-F', '--fanout', default=3, help='Max number of hash pairs per peak')
@click.option('-P', '--pks-per-frame', default=5, help='Maximum number of peaks per frame')
@click.option('-D', '--search-depth', default=100, help='How far down to search raw matching track list')
@click.option('-H', '--ncores', default=1, help='Number of processes to use')
@click.option('-o', '--opfile', default='', help='Write output (matches) to this file, not stdout')
@click.option('-K', '--precompute-peaks', is_flag=True, help='Precompute just landmarks (else full hashes)')
@click.option('-k', '--skip-existing', is_flag=True, help='On precompute, skip items if output file already exists')
@click.option('-C', '--continue-on-error', is_flag=True, help='Keep processing despite errors reading input')
@click.option('-l', '--list', is_flag=True, help='Input files are lists, not audio')
@click.option('-T', '--sortbytime', is_flag=True, help='Sort multiple hits per file by time (instead of score)')
@click.option('-v', '--verbose', type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']), default='INFO', help='Set the level of verbosity')
@click.option('-I', '--illustrate', is_flag=True, help='Make a plot showing the match')
@click.option('-J', '--illustrate-hpf', is_flag=True, help='Plot the match, using onset enhancement')
@click.option('-W', '--wavdir', default='', help='Find sound files under this dir')
@click.option('-V', '--wavext', default='', help='Extension to add to wav file names')
@click.version_option(version=__version__, prog_name='audfprint-enhanced')
@click.argument('file', nargs=-1)
def main(cmd, dbase, density, hashbits, bucketsize, maxtime, maxtimebits, samplerate, precompdir, shifts, match_win, min_count, max_matches, exact_count, find_time_range, time_quantile, freq_sd, fanout, pks_per_frame, search_depth, ncores, opfile, precompute_peaks, skip_existing, continue_on_error, list, sortbytime, verbose, illustrate, illustrate_hpf, wavdir, wavext, file):
    # Setup output function
    if opfile:
        logger.add(opfile)

    logger.level(os.getenv('LOG_LEVEL', verbose))

    # Keep track of wall time
    initticks = time_clock()

    if not maxtimebits:
        maxtimebits = hash_table._bitsfor(maxtime)

    # Setup the analyzer if we're using one (i.e., unless "merge")
    analyzer = setup_analyzer(density, cmd == "match", pks_per_frame, fanout, freq_sd, shifts, samplerate, continue_on_error) if cmd not in ["merge", "newmerge", "list", "remove"] else None

    precomp_type = 'hashes' if not precompute_peaks else 'peaks'

    # Set up the hash table, if we're using one (i.e., unless "precompute")
    if cmd != "precompute":
        # For everything other than precompute, we need a database name
        # Check we have one
        if not dbase:
            raise ValueError("dbase name must be provided if not precompute")
        if cmd in ["new", "newmerge"]:
            # Check that the output directory can be created before we start
            ensure_dir(os.path.split(dbase)[0])
            # Create a new hash table
            hash_tab = hash_table.HashTable(
                    hashbits=hashbits,
                    depth=bucketsize,
                    maxtime=(1 << maxtimebits))
            # Set its samplerate param
            if analyzer:
                hash_tab.params['samplerate'] = analyzer.target_sr

        else:
            logger.trace([time.ctime() + " Reading hash table " + dbase])
            hash_tab = hash_table.HashTable(dbase)
            if analyzer and 'samplerate' in hash_tab.params \
                    and hash_tab.params['samplerate'] != analyzer.target_sr:
                logger.debug("db samplerate overridden to ", analyzer.target_sr)
    else:
        # The command IS precompute
        # dummy empty hash table
        hash_tab = None

    # Create a matcher
    matcher = setup_matcher(match_win, search_depth, min_count, max_matches, exact_count, find_time_range, time_quantile, sortbytime, illustrate, illustrate_hpf) if cmd == 'match' else None

    filename_iter = filename_list_iterator(
            file, wavdir, wavext, list)

    #######################
    # Run the main commmand
    #######################

    # How many processors to use (multiprocessing)
    if ncores > 1 and cmd not in ["merge", "newmerge", "list", "remove"]:
        # merge/newmerge/list/remove are always single-thread processes
        do_cmd_multiproc(cmd, analyzer, hash_tab, filename_iter,
                         matcher, precompdir,
                         precomp_type,
                         skip_existing=skip_existing,
                         strip_prefix=wavdir,
                         ncores=ncores)
    else:
        do_cmd(cmd, analyzer, hash_tab, filename_iter,
               matcher, precompdir, precomp_type,
               skip_existing=skip_existing,
               strip_prefix=wavdir)

    elapsedtime = time_clock() - initticks
    if analyzer and analyzer.soundfiletotaldur > 0.:
        logger.debug("Processed "
              + "%d files (%.1f s total dur) in %.1f s sec = %.3f x RT" \
              % (analyzer.soundfilecount, analyzer.soundfiletotaldur,
                 elapsedtime, (elapsedtime / analyzer.soundfiletotaldur)))

    # Save the hash table file if it has been modified
    if hash_tab and hash_tab.dirty:
        # We already created the directory, if "new".
        hash_tab.save(dbase)


if __name__ == '__main__':
    main()
