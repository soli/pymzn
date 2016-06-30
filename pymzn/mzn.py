# -*- coding: utf-8 -*-
"""Wrapper module for the MiniZinc tool pipeline."""
import contextlib
import inspect
import logging
import os.path
import shutil
import tempfile
from io import IOBase

from pymzn.binary import run, command, BinaryRuntimeError
from pymzn.dzn import dzn, parse_dzn

soln_sep_default = '----------'
search_complete_msg_default = '=========='
unsat_msg_default = '=====UNSATISFIABLE====='
unkn_msg_default = '=====UNKNOWN====='
unbnd_msg_default = '=====UNBOUNDED====='


# TODO: mzn2doc
# TODO: optimatsat
# TODO: check all the documentation!!!!!


def solns2out(solns_input, ozn_file=None, parse=parse_dzn,
              solns2out_cmd='solns2out', soln_sep=soln_sep_default,
              unbnd_msg=unbnd_msg_default, unkn_msg=unkn_msg_default,
              unsat_msg=unsat_msg_default,
              search_complete_msg=search_complete_msg_default):
    """
    Wraps the MiniZinc utility solns2out, executes it on the input solution
    stream, then parses and returns the output.

    :param parse_solns:
    :param file-like solns_input: The solution stream as output by the solver
    :param str ozn_file: The .ozn file path produced by the mzn2fzn utility
    :param str output_file: The file path where to write the output of
                            solns2out; defaults to None which outputs to
                            stdout, and thus the output is directly parsed
                            by this function and not saved on a file
    :param func parse: The function that parses the output of the solns2out
                       utility, if None a list of unparsed solution strings is
                       returned; by default the function parse_dzn is used,
                       which can be used only if no output statement is used in
                       the MiniZinc model
    :param str solns2out_cmd: The command to call to execute the solns2out
                              utility; defaults to 'solns2out', assuming the
                              utility is the PATH
    :param str soln_sep: The line separating each solution in the solver output
                         stream; defaults to the default value for solns2out
    :param str search_complete_msg: The line message for search complete in the
                                    solver output stream; defaults to the
                                    default value for solns2out
    :param str unkn_msg: The line message for unknown solution in the solver
                         output stream; defaults to the default value for
                         solns2out
    :param str unsat_msg: The line message for unsatisfiable problem in the
                          solver output stream; defaults to the default
                          value for solns2out
    :param str unbnd_msg: The line message for unbounded problem in the
                          solver output stream; defaults to the default
                          value for solns2out
    :return: A list of solutions output by the solns2out utility; if a
             parsing function is provided, the solutions are parsed,
             otherwise a list of solution strings (as output by solns2out)
             is returned
    :rtype: list
    """
    log = logging.getLogger(__name__)

    if ozn_file:
        args = [ozn_file]

        log.debug('Calling %s with arguments: %s', solns2out_cmd, args)
        cmd = command(solns2out_cmd, args)

        try:
            out = run(cmd, cmd_in=solns_input)
        except BinaryRuntimeError:
            log.exception('')
            raise
    else:
        out = solns_input

    lines = out.decode('ascii').split('\n')

    solns = []
    curr_out = []
    unsat = False
    unkn = False
    unbnd = False

    for line in lines:
        line = line.strip()
        if line == soln_sep:
            if parse:
                sol = parse(curr_out)
                solns.append(sol)
                log.debug('Solution found: %s', sol)
            else:
                sol = '\n'.join(curr_out)
                solns.append(sol)
                log.debug('Solution found: %s', sol)
            curr_out = []
        elif line == search_complete_msg:
            break
        elif line == unkn_msg:
            unkn = True
            break
        elif line == unsat_msg:
            unsat = True
            break
        elif line == unbnd_msg:
            unbnd = True
            break
        else:
            curr_out.append(line)

    if unkn:
        raise MiniZincUnknownError()
    if unsat:
        raise MiniZincUnsatisfiableError()
    if unbnd:
        raise MiniZincUnboundedError()

    if len(solns) == 0:
        log.warning('A solution was found but none was returned by the '
                    'solver or the parser.')

    return solns


def mzn2fzn(mzn, keep=False, output_base=None, data=None, dzn_files=None,
            no_ozn=False, mzn_globals='gecode', mzn2fzn_cmd='mzn2fzn'):
    """
    Flatten a MiniZinc model into a FlatZinc one. It executes the mzn2fzn
    utility from libmzn to produce a fzn file from a mzn one (and possibly
    an ozn file as well).

    :param str mzn: The path to a mzn file containing the MiniZinc model
    :param bool keep: Whether to keep the generated fzn and ozn files or not;
                      not keeping the files actually means creating a temporary
                      directory to store the generated files, which will then
                      be cleaned up by the OS. Default is False. Running
                      minizinc with keep=False automatically ensures isolation
                      of the instances of your solver. If you specify keep=True
                       you need to take care of the isolation yourself by
                       specifying a proper output_base for each instance of
                       the solver.
    :param dict data: Dictionary of variables to use as data for the solving
                      of the minizinc problem
    :param [str] dzn_files: A list of paths to dzn files to attach to the
                            mzn2fzn execution; by default no data file is
                            attached
    :param str output_base: The base name for the fzn and ozn files (extension
                            are then attached automatically); by default the
                            mzn_file name is used
    :param str mzn_globals: The path to the directory to search for globals
                            included files; by default the 'gecode' global
                            library is used, since this library assumes Gecode
                            as default solver
    :param str mzn2fzn_cmd: The command to call to execute the mzn2fzn utility;
                            defaults to 'mzn2fzn', assuming the utility is the
                            PATH
    :return: The paths to the mzn, fzn and ozn files created by the function
    :rtype: (str, str, str)
    """
    log = logging.getLogger(__name__)

    if not mzn:
        raise ValueError('MiniZinc model not specified.')

    args = []
    mzn_file = None

    if isinstance(mzn, MiniZincModel):
        mzn = mzn.compile()

    if keep:
        if _is_mzn_file(mzn):
            mzn_file = mzn
            log.debug('Mzn file provided: %s', mzn_file)
        elif isinstance(mzn, (str, IOBase)):
            if output_base:
                mzn_file_name = output_base + '.mzn'
                mzn_file = open(mzn_file_name, 'w+')
                output_base = None
            else:
                mzn_file_name = 'mznout.mzn'
                mzn_file = open(mzn_file_name, 'w+')
            log.debug('Writing provided content to: %s', mzn_file_name)
            mzn_file.write(mzn)
            mzn_file.close()
            mzn_file = mzn_file_name
    else:
        if _is_mzn_file(mzn):
            mzn_file = mzn
            prex_mzn = os.path.basename(mzn_file)[:-4] + '_'
            dir_mzn = os.path.dirname(mzn_file)
            tmp_mzn_file = tempfile.NamedTemporaryFile(prefix=prex_mzn,
                                                       suffix='.mzn',
                                                       dir=dir_mzn, mode='w+',
                                                       delete=False)
            log.debug('Copying %s to %s (keep=False)', mzn_file,
                      tmp_mzn_file.name)
            with open(mzn_file) as f:
                shutil.copyfileobj(f, tmp_mzn_file)
                tmp_mzn_file.file.flush()
            mzn_file = tmp_mzn_file.name
        elif isinstance(mzn, (str, IOBase)):
            if output_base:
                prex_mzn = os.path.basename(output_base) + '_'
                dir_mzn = os.path.dirname(output_base)
            else:
                prex_mzn = 'mznout_'
                dir_mzn = '.'
            tmp_mzn_file = tempfile.NamedTemporaryFile(prefix=prex_mzn,
                                                       suffix='.mzn',
                                                       dir=dir_mzn, mode='w+',
                                                       delete=False)
            mzn_file_name = tmp_mzn_file.name
            log.debug('Writing provided content to: %s', mzn_file_name)
            tmp_mzn_file.write(mzn)
            tmp_mzn_file.close()
            mzn_file = mzn_file_name
        output_base = mzn_file[:-4]
        args.append(('--output-base', output_base))

    if not mzn_file:
        raise TypeError('The specified MiniZinc model is not valid.')

    if output_base:
        args.append(('--output-base', output_base))

    if mzn_globals:
        args.append(('-G', mzn_globals))

    if no_ozn:
        args.append('--no-output-ozn')

    if data is not None:
        data = '"' + ' '.join(dzn(data)) + '"'
        args.append(('-D', data))

    dzn_files = dzn_files or []
    args += [mzn_file] + dzn_files

    assert os.path.isfile(mzn_file), 'Input mzn file does not exists.'

    log.debug('Calling %s with arguments: %s', mzn2fzn_cmd, args)
    cmd = command(mzn2fzn_cmd, args)

    try:
        run(cmd)
    except BinaryRuntimeError:
        log.exception('')
        raise

    base = output_base or mzn_file[:-4]
    out_files = [mzn_file]

    fzn_file = '.'.join([base, 'fzn'])
    if os.path.isfile(fzn_file):
        out_files.append(fzn_file)
    else:
        out_files.append(None)

    ozn_file = '.'.join([base, 'ozn'])
    if not no_ozn and os.path.isfile(ozn_file):
        out_files.append(ozn_file)
    else:
        out_files.append(None)

    return tuple(out_files)


def fzn_gecode(fzn_file, time=0, parallel=1, n_solns=-1, seed=0,
               fzn_gecode_cmd='fzn-gecode', suppress_segfault=False,
               restart=None, restart_base=None, restart_scale=None):
    """
    Solves a constrained optimization problem using the Gecode solver,
    provided a .fzn input problem file.

    :param str fzn_file: The path to the fzn file containing the problem to be
                         solved
    :param str output_file: The file where to write the solution output stream
                            of Gecode; if None (default) the stream is sent to
                            the standard output and directly returned by this
                            function, without saving it on a file
    :param str fzn_gecode_cmd: The command to call to execute the fzn-gecode
                               program; defaults to 'fzn-gecode', assuming the
                               program is the PATH
    :param int n_solns: The number of solutions to output (0 = all,
                        -1 = one/best); default is -1
    :param int parallel: The number of threads to use to solve the problem (0 =
                         #processing units); default is 1
    :param int time: The time cutoff in milliseconds, after which the
                     execution is truncated and the best solution so far is
                     returned, 0 means no time cutoff; default is 0
    :param int seed: random seed; default is 0
    :param str restart: restart sequence type; default is None
    :param str restart_base: base for geometric restart sequence; if None (
                             default) the default value of Gecode is used,
                             which is 1.5
    :param str restart_scale: scale factor for restart sequence; if None (
                              default) the default value of Gecode is used,
                              which is 250
    :param suppress_segfault: whether to accept or not a solution returned
                              when a segmentation fault has happened (this
                              is unfortunately necessary sometimes due to
                              some bugs in gecode).
    :return: A binary string containing the solution output stream of the
             execution of Gecode on the specified problem; it can be
             directly be given to the function solns2out or it can be read
             as a string using `out.decode('ascii')`
    :rtype: str
    """
    log = logging.getLogger(__name__)
    args = []
    if n_solns >= 0:
        args.append(('-n', n_solns))
    if parallel != 1:
        args.append(('-p', parallel))
    if time > 0:
        args.append(('-time', time))
    if seed != 0:
        args.append(('-r', seed))
    if restart:
        args.append(('-restart', restart))
    if restart_base:
        args.append(('-restart-base', restart_base))
    if restart_scale:
        args.append(('-restart-scale', restart_scale))
    args.append(fzn_file)

    assert os.path.isfile(fzn_file), 'Input fzn file does not exists.'

    log.debug('Calling %s with arguments: %s', fzn_gecode_cmd, args)
    cmd = command(fzn_gecode_cmd, args)

    try:
        solns = run(cmd)
    except BinaryRuntimeError as bin_err:
        err_msg = bin_err.err_msg
        if (suppress_segfault and len(bin_err.out) > 0 and
                err_msg.startswith('Segmentation fault')):
                log.warning('Gecode returned error code {} (segmentation '
                            'fault) but a solution was found and returned '
                            '(suppress_segfault=True).'.format(bin_err.ret))
                solns = bin_err.out
        else:
            log.exception('Gecode returned error code {} '
                          '(segmentation fault).'.format(bin_err.ret))
            raise bin_err
    return solns


def minizinc(mzn, bin_path=None, keep=False, output_base=None,
             fzn_cmd=fzn_gecode, fzn_flags=None, parse_solns=False,
             warn_on_unsolved=False, **kwargs):
    """
    Workflow to solve a constrained optimization problem encoded with MiniZinc.
    It first calls mzn2fzn to get the fzn and ozn files, then calls the
    solver using the specified fzn_cmd, passing the fzn_flags,
    then it calls the solns2out utility on the output of the solver.

    :param warn_on_unsolved:
    :param output_base:
    :param str mzn: The mzn file specifying the problem to be solved
    :param bool keep: Whether to keep the generated fzn and ozn files or not;
                      not keeping the files actually means creating a temporary
                      directory to store the generated files, which will then
                      be cleaned up by the OS. Default is False. Running
                      minizinc with keep=False automatically ensures isolation
                      of the instances of your solver. If you specify keep=True
                       you need to take care of the isolation yourself by
                       specifying a proper output_base for each instance of
                       the solver.
    :param str bin_path: The path to the directory containing the binaries of
                         the libminizinc utilities
    :param func fzn_cmd: The function to call for the solver; defaults to the
                         function fzn_gecode
    :param fzn_flags: A dictionary containing the additional flags to
                      pass to the fzn_cmd; default is None, meaning no
                      additional attribute
    :param kwargs: Any additional keyword argument is passed to the mzn2fzn
                   and solns2out utilities as options
    :return: Returns the solutions as returned by the solns2out utility
    :rtype: [str] or [dict]
    """
    log = logging.getLogger(__name__)

    mzn2fzn_defaults = _get_defaults(mzn2fzn)
    mzn2fzn_kwargs = set(mzn2fzn_defaults.keys())
    mzn2fzn_args = _sub_dict(kwargs, mzn2fzn_kwargs)
    mzn2fzn_def_cmd = mzn2fzn_defaults['mzn2fzn_cmd']
    mzn2fzn_cmd = mzn2fzn_args.get('mzn2fzn_cmd', mzn2fzn_def_cmd)

    # Adjust the path if bin_path is provided
    if bin_path:
        mzn2fzn_path = os.path.join(bin_path, mzn2fzn_cmd)
        mzn2fzn_args['mzn2fzn_cmd'] = mzn2fzn_path

    mzn2fzn_args['keep'] = keep
    mzn2fzn_args['output_base'] = output_base
    mzn2fzn_args['no_ozn'] = parse_solns

    # Execute mzn2fzn
    mzn_file, fzn_file, ozn_file = mzn2fzn(mzn, **mzn2fzn_args)

    if not fzn_flags:
        fzn_flags = {}

    try:
        # Execute fzn_cmd
        solns = fzn_cmd(fzn_file, **fzn_flags)
        solns2out_defaults = _get_defaults(solns2out)
        solns2out_kwargs = set(solns2out_defaults.keys())
        solns2out_args = _sub_dict(kwargs, solns2out_kwargs)
        solns2out_def_cmd = solns2out_defaults['solns2out_cmd']
        solns2out_cmd = solns2out_args.get('solns2out_cmd',
                                           solns2out_def_cmd)

        # Adjust the path if bin_path is provided
        if bin_path:
            solns2out_path = os.path.join(bin_path, solns2out_cmd)
            solns2out_args['solns2out_cmd'] = solns2out_path

        # Execute solns2out
        # if parse_solns = True then ozn_file is put to None by mzn2fzn
        # so if it is None, it tries to parse the solver solution stream
        out = solns2out(solns, ozn_file, **solns2out_args)
    except (MiniZincUnsatisfiableError, MiniZincUnknownError,
            MiniZincUnboundedError) as err:
        if warn_on_unsolved:
            log.warning('No solution found. {}'.format(err.message))
            out = None
        else:
            log.exception('')
            raise
    finally:
        if not keep:
            with contextlib.suppress(FileNotFoundError):
                os.remove(mzn_file)
                os.remove(fzn_file)
                os.remove(ozn_file)
                log.debug('Deleting files: %s %s %s',
                          mzn_file, fzn_file, ozn_file)
    return out


class MiniZincModel(object):

    def __init__(self, file_mzn=None):
        self.file_mzn = file_mzn
        self.vars = {}
        self.constraints = []
        self.solve = None
        self.output = None

    def add_constraint(self, c):
        self.constraints.append(c)

    def solve_statement(self, solve):
        self.solve = solve

    def output_statement(self, output):
        self.output = output

    def add_variable(self, vartype, var, val=None):
        self.vars[var] = (vartype, val)

    def compile(self):
        model = []

        if self.file_mzn and os.path.exists(self.file_mzn):
            with open(self.file_mzn) as f:
                model = f.readlines()

        model.append('\n%% GENERATED BY PYMZN %%\n')

        for var, (vartype, val) in self.vars.items():
            if val is not None:
                model.append('{}: {} = {};'.format(vartype, var, val))
            else:
                model.append('{}: {};'.format(vartype, var))

        for constr in self.constraints:
            model.append('constraint {};'.format(constr))

        if self.solve is not None:
            model.append('solve {};'.format(self.solve))

        if self.output is not None:
            model.append('output {};'.format(self.output))

        return '\n'.join(model)


def _is_mzn_file(mzn):
    return isinstance(mzn, str) and mzn.endswith('.mzn')


def _get_defaults(f):
    spec = inspect.getfullargspec(f)
    return dict(zip(reversed(spec.args), reversed(spec.defaults)))


def _sub_dict(d, keys):
    # creates a subset of d containing only the specified keys
    return {k: d[k] for k in d if k in keys}


class MiniZincUnsatisfiableError(RuntimeError):
    """
    Error raised when a minizinc problem is unsatisfiable.
    """

    def __init__(self):
        super().__init__('The problem is unsatisfiable.')


class MiniZincUnknownError(RuntimeError):
    """
    Error raised when minizinc returns no solution (unknown).
    """

    def __init__(self):
        super().__init__('The solution of the problem is unknown.')


class MiniZincUnboundedError(RuntimeError):
    """
    Error raised when a minizinc problem is unbounded.
    """

    def __init__(self):
        super().__init__('The problem is unbounded.')
