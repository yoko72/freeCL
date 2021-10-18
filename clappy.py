from sys import exit, argv
from gettext import gettext as _
from typing import Optional
import logging
import argparse
from argparse import *
import functools
import inspect


logger = logging.getLogger(__name__)
HELP_OPTIONS = ["-h", "--help"]
SUBCOMMAND = "_subcommand_dest_for_clappy"

if len(argv) == 2 and argv[1].lower() in HELP_OPTIONS:
    runs_with_help_option = True
    argv[1] = argv[1].lower()
else:
    runs_with_help_option = False


def normalize_bound_of_args(func):
    """Same hash by same values.
    This converts each keyword arg to positional arg as much as possible.
    If default arguments don't get actual argument, explicitly give the default value.
    """
    signature = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return func(*bound.args, **bound.kwargs)
    return wrapper


def _get_action_name(argument):
    if argument is None:
        return None
    elif argument.option_strings:
        return '/'.join(argument.option_strings)
    elif argument.metavar not in (None, SUPPRESS):
        return argument.metavar
    elif argument.dest not in (None, SUPPRESS):
        return argument.dest
    else:
        return None


class Parser(argparse.ArgumentParser):
    dest_name = SUBCOMMAND  # for detecting which subcommand is invoked
    is_verbose = True
    VERBOSE_VALUE_CHANGE_MESSAGE = '''
    While parsing {input_arg_name}, the value of "{attr}" changed from {last_time} to {this_time}.
This usually happens because of similar names of arguments or confusing order of arguments.
Consider to change them if you actually got invalid result.'''
    VALUE_CHANGE_MESSAGE = '''"{attr}" changed from {last_time} to {this_time}.'''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.args_for_parse: list = argv[1::]
        self.kwargs_on_parse: Optional[dict] = {}
        self.namespace = None
        self.parsed_result = dict()
        self.subparsers_list = []
        self.subparsers_action = None
        self.index_of_subcommand = None

    @normalize_bound_of_args
    @functools.lru_cache()  # so that parse can be called multiple times to get result in multiple places.
    def parse(self, *args, is_flag=False, **kwargs):
        if self.subparsers_list:
            parser = [subparser for subparser in self.subparsers_list if subparser.invoked][0]
            if parser:
                parser.args_for_parse = argv[self.index_of_subcommand+2::]
                return parser.parse(*args, is_flag=is_flag, **kwargs)
            else:
                return None
        if is_flag:
            if kwargs.get("action", None) is None:
                kwargs["action"] = "store_true"
            else:
                raise TypeError(f"{parse.__name__} got multiple values for action, "
                                f"since is_flag=True is alias of action='store_true'.")
        try:
            self.add_argument(*args, **kwargs)
        except argparse.ArgumentError as e:
            if e.message.startswith("conflicting"):
                msg = \
                    f"""Tried to register same arguments.
Usually, the cache is returned in such case. However, cache is not returned this time 
since some of given arguments are different from last time. 
Use same arguments for {self.parse.__name__}() to get cache."""
                raise ValueError(msg) from e
        if not runs_with_help_option:
            latest_namespace, unrecognized_args = self.parse_known_args()
            for action in self._option_string_actions.values():
                if hasattr(action, "parsed_currently"):
                    action.parsed_currently = False
            if len(args) > 1:
                lengths = map(len, args)
                max_length = max(lengths)
                input_arg_name = [arg for arg in args if len(arg) == max_length][0]
            elif len(args) == 1:
                input_arg_name = args[0]
            else:
                input_arg_name = kwargs.get("dest", None)
                if input_arg_name is None:
                    raise TypeError("Name of arg was not given. "
                                    "Positional argument or keyword argument for 'dest' is required.")

            # noinspection PyUnboundLocalVariable
            arg = input_arg_name.lstrip(self.prefix_chars)
            logger.debug(f"Unrecognized args while parsing {arg}: {unrecognized_args}")
            if self.namespace:
                for attr in self.namespace.__dict__:
                    last_time = getattr(self.namespace, attr)
                    this_time = getattr(latest_namespace, attr)
                    if last_time != this_time:
                        logger.error(self.VALUE_CHANGE_MESSAGE.format(input_arg_name=input_arg_name, attr=attr,
                                                                      last_time=last_time, this_time=this_time))
            self.namespace = latest_namespace
            return getattr(latest_namespace, arg)

    def parse_known_args(self, *args, **kwargs):
        if args or kwargs:
            return super().parse_known_args(*args, **kwargs)
        else:
            return super().parse_known_args(self.args_for_parse, **self.kwargs_on_parse)

    def _parse_known_args(self, arg_strings, namespace):
        """Almost same as super()._parse_known_args.
        This differs only at line 137 rows below and 156 rows below."""

        # replace arg strings that are file references
        if self.fromfile_prefix_chars is not None:
            arg_strings = self._read_args_from_files(arg_strings)

        # map all mutually exclusive arguments to the other arguments
        # they can't occur with
        action_conflicts = {}
        for mutex_group in self._mutually_exclusive_groups:
            group_actions = mutex_group._group_actions
            for i, mutex_action in enumerate(mutex_group._group_actions):
                conflicts = action_conflicts.setdefault(mutex_action, [])
                conflicts.extend(group_actions[:i])
                conflicts.extend(group_actions[i + 1:])

        # find all option indices, and determine the arg_string_pattern
        # which has an 'O' if there is an option at an index,
        # an 'A' if there is an argument, or a '-' if there is a '--'
        option_string_indices = {}
        arg_string_pattern_parts = []
        arg_strings_iter = iter(arg_strings)
        for i, arg_string in enumerate(arg_strings_iter):

            # all args after -- are non-options
            if arg_string == '--':
                arg_string_pattern_parts.append('-')
                for arg_string in arg_strings_iter:
                    arg_string_pattern_parts.append('A')

            # otherwise, add the arg to the arg strings
            # and note the index if it was an option
            else:
                option_tuple = self._parse_optional(arg_string)
                if option_tuple is None:
                    pattern = 'A'
                else:
                    option_string_indices[i] = option_tuple
                    pattern = 'O'
                arg_string_pattern_parts.append(pattern)

        # join the pieces together to form the pattern
        arg_strings_pattern = ''.join(arg_string_pattern_parts)

        # converts arg strings to the appropriate and then takes the action
        seen_actions = set()
        seen_non_default_actions = set()

        def take_action(action, argument_strings, option_string=None):
            seen_actions.add(action)
            argument_values = self._get_values(action, argument_strings)

            # error if this argument is not allowed with other previously
            # seen arguments, assuming that actions that use the default
            # value don't really count as "present"
            if argument_values is not action.default:
                seen_non_default_actions.add(action)
                for conflict_action in action_conflicts.get(action, []):
                    if conflict_action in seen_non_default_actions:
                        msg = _('not allowed with argument %s')
                        action_name = _get_action_name(conflict_action)
                        raise ArgumentError(action, msg % action_name)

            # take the action if we didn't receive a SUPPRESS value
            # (e.g. from a default)
            if argument_values is not SUPPRESS:
                action(self, namespace, argument_values, option_string)

        # function to convert arg_strings into an optional action
        def consume_optional(start_index):

            # get the optional identified at this index
            option_tuple = option_string_indices[start_index]
            action, option_string, explicit_arg = option_tuple

            # identify additional optionals in the same arg string
            # (e.g. -xyz is the same as -x -y -z if no args are required)
            match_argument = self._match_argument
            action_tuples = []
            while True:

                # if we found no optional action, skip it
                if action is None:
                    extras.append(arg_strings[start_index])
                    return start_index + 1

                # if there is an explicit argument, try to match the
                # optional's string arguments to only this
                if explicit_arg is not None:
                    arg_count = match_argument(action, 'A')

                    # if the action is a single-dash option and takes no
                    # arguments, try to parse more single-dash options out
                    # of the tail of the option string
                    chars = self.prefix_chars
                    if arg_count == 0 and option_string[1] not in chars:
                        action_tuples.append((action, [], option_string))
                        char = option_string[0]
                        option_string = char + explicit_arg[0]
                        new_explicit_arg = explicit_arg[1:] or None
                        optionals_map = self._option_string_actions
                        if option_string in optionals_map:
                            action = optionals_map[option_string]
                            explicit_arg = new_explicit_arg
                        else:
                            msg = _('ignored explicit argument %r')
                            raise ArgumentError(action, msg % explicit_arg)

                    # if the action expect exactly one argument, we've
                    # successfully matched the option; exit the loop
                    elif arg_count == 1:
                        stop = start_index + 1
                        args = [explicit_arg]
                        action_tuples.append((action, args, option_string))
                        break

                    # error if a double-dash option did not use the
                    # explicit argument
                    else:
                        msg = _('ignored explicit argument %r')
                        raise ArgumentError(action, msg % explicit_arg)

                # if there is no explicit argument, try to match the
                # optional's string arguments with the following strings
                # if successful, exit the loop
                else:
                    start = start_index + 1
                    selected_patterns = arg_strings_pattern[start:]
                    arg_count = match_argument(action, selected_patterns)
                    stop = start + arg_count
                    args = arg_strings[start:stop]
                    action_tuples.append((action, args, option_string))
                    break

            # add the Optional to the list and return the index at which
            # the Optional's string args stopped
            assert action_tuples
            # ~~~~~~~~~~~~~~~~~ only here is modified from argparse.ArgumentParser._parse_known_args ~~~~~~~~~~~~~~~~~
            self._run_if_not_parsed(namespace, take_action, explicit_arg, arg_strings, action_tuples)
            return stop

        # the list of Positionals left to be parsed; this is modified
        # by consume_positionals()
        positionals = self._get_positional_actions()

        # function to convert arg_strings into positional actions
        def consume_positionals(start_index):
            # match as many Positionals as possible
            match_partial = self._match_arguments_partial
            selected_pattern = arg_strings_pattern[start_index:]
            arg_counts = match_partial(positionals, selected_pattern)

            # slice off the appropriate arg strings for each Positional
            # and add the Positional and its args to the list
            for action, arg_count in zip(positionals, arg_counts):
                args = arg_strings[start_index: start_index + arg_count]
                self._memorize_index_of_subcommand(action, start_index)
                start_index += arg_count
                take_action(action, args)

            # slice off the Positionals that we just parsed and return the
            # index at which the Positionals' string args stopped
            positionals[:] = positionals[len(arg_counts):]
            return start_index

        # consume Positionals and Optionals alternately, until we have
        # passed the last option string
        extras = []
        start_index = 0
        if option_string_indices:
            max_option_string_index = max(option_string_indices)
        else:
            max_option_string_index = -1
        while start_index <= max_option_string_index:

            # consume any Positionals preceding the next option
            next_option_string_index = min([
                index
                for index in option_string_indices
                if index >= start_index])
            if start_index != next_option_string_index:
                positionals_end_index = consume_positionals(start_index)

                # only try to parse the next optional if we didn't consume
                # the option string during the positionals parsing
                if positionals_end_index > start_index:
                    start_index = positionals_end_index
                    continue
                else:
                    start_index = positionals_end_index

            # if we consumed all the positionals we could and we're not
            # at the index of an option string, there were extra arguments
            if start_index not in option_string_indices:
                strings = arg_strings[start_index:next_option_string_index]
                extras.extend(strings)
                start_index = next_option_string_index

            # consume the next optional and any arguments for it
            start_index = consume_optional(start_index)

        # consume any positionals following the last Optional
        stop_index = consume_positionals(start_index)

        # if we didn't consume all the argument strings, there were extras
        extras.extend(arg_strings[stop_index:])

        # make sure all required actions were present and also convert
        # action defaults which were not given as arguments
        required_actions = []
        for action in self._actions:
            if action not in seen_actions:
                if action.required:
                    required_actions.append(_get_action_name(action))
                else:
                    # Convert action default now instead of doing it before
                    # parsing arguments to avoid calling convert functions
                    # twice (which may fail) if the argument was given, but
                    # only if it was defined already in the namespace
                    if (action.default is not None and
                        isinstance(action.default, str) and
                        hasattr(namespace, action.dest) and
                        action.default is getattr(namespace, action.dest)):
                        setattr(namespace, action.dest,
                                self._get_value(action, action.default))

        if required_actions:
            self.error(_('the following arguments are required: %s') %
                       ', '.join(required_actions))

        # make sure all required groups had one option present
        for group in self._mutually_exclusive_groups:
            if group.required:
                for action in group._group_actions:
                    if action in seen_non_default_actions:
                        break

                # if no actions were used, report the error
                else:
                    names = [_get_action_name(action)
                             for action in group._group_actions
                             if action.help is not SUPPRESS]
                    msg = _('one of the arguments %s is required')
                    self.error(msg % ' '.join(names))

        # return the updated namespace and the extra arguments
        return namespace, extras

    def _memorize_index_of_subcommand(self, action, start_index):
        if action.dest == SUBCOMMAND:
            self.index_of_subcommand = start_index

    def _run_if_not_parsed(self, namespace, take_action, explicit_arg, arg_strings, action_tuples):
        for action, args, option_string in action_tuples:
            if hasattr(action, "parsed_currently") and action.parsed_currently is True:
                continue
            take_action(action, args, option_string)
            option_name = option_string.lstrip("-")
            if explicit_arg is None or f"{option_string}+{explicit_arg}" in arg_strings:
                if hasattr(namespace, option_name) and getattr(namespace, option_name) is not None:
                    registered_actions = self._registries["action"]
                    if not isinstance(action, (registered_actions["append"], registered_actions["extend"])):
                        action.parsed_currently = True

    def _get_values(self, action, arg_strings):
        """Almost same as super()._get_values.
        This differs only at line 43 rows below that runs '_get_value_for_subcommand'."""
        if action.nargs not in [PARSER, REMAINDER]:
            try:
                arg_strings.remove('--')
            except ValueError:
                pass

        if not arg_strings and action.nargs == OPTIONAL:
            if action.option_strings:
                value = action.const
            else:
                value = action.default
            if isinstance(value, str):
                value = self._get_value(action, value)
                self._check_value(action, value)

        elif (not arg_strings and action.nargs == ZERO_OR_MORE and
              not action.option_strings):
            if action.default is not None:
                value = action.default
            else:
                value = arg_strings
            self._check_value(action, value)

        elif len(arg_strings) == 1 and action.nargs in [None, OPTIONAL]:
            arg_string, = arg_strings
            value = self._get_value(action, arg_string)
            self._check_value(action, value)

        elif action.nargs == REMAINDER:
            value = [self._get_value(action, v) for v in arg_strings]

        elif action.nargs == PARSER:
            value = [self._get_value(action, v) for v in arg_strings]
            self._check_if_subcommand_included(action, value)  # modified from argparse
            # original is following.
            # self._check_value(action, value[0])

        elif action.nargs == SUPPRESS:
            value = SUPPRESS

        else:
            value = [self._get_value(action, v) for v in arg_strings]
            for v in value:
                self._check_value(action, v)

        return value

    def _check_if_subcommand_included(self, action, given_args: list):
        """Checks all given_args if subcommand is included or not.
        If not, raise SubCommandNotFound."""
        for arg in given_args:
            try:
                self._check_value(action, arg)
            except ArgumentError:
                continue
            else:
                return
        if action.choices:

            if isinstance(action.choices, dict):
                action_names = list(action.choices.keys())
            else:
                action_names = list(action.choices)
            msg = f"Failed to find subcommand {action_names} in args: {given_args}"
        else:
            msg = f"Valid value for {str(action)} is not found in value: {given_args}"
        raise SubCommandNotFound(msg)

    def add_subparsers(self, *, dest=None, parser_class=None, **kwargs):
        dest = dest or self.dest_name
        parser_class = parser_class or SubCommand
        self.subparsers_action = super().add_subparsers(dest=dest, parser_class=parser_class, **kwargs)
        return self.subparsers_action


class SubCommandNotFound(Exception):
    def __init__(self, message):
        self.message = message


class SubCommand(Parser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoked = False

    def __bool__(self):
        return self.invoked


class NotInvoked(Exception):
    pass


_parser: Optional[Parser] = None


def initialize_parser(*args, **kwargs):
    global _parser
    if _parser is not None:
        if isinstance(_parser, Parser):
            raise ValueError("Tried to initialize parser although it is already initialized."
                             "If you want to remake it, set_parser(None) first, then initialize again.")
        else:
            raise ValueError(f"Tried to initialize parser, but parser is already not None."
                             f"Something wrong happened. The type of parser is {type(_parser)}.")
    if kwargs.get("add_help", None) is not None:
        add_help = kwargs["add_help"]
    else:
        if runs_with_help_option:
            add_help = True
        else:
            add_help = False
    _parser = Parser(*args, add_help=add_help, **kwargs)
    return _parser


def init_parser(func):
    global _parser

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if _parser is None:
            initialize_parser()
        return func(*args, **kwargs)
    return wrapper


def clear_actions():
    global _parser
    if _parser:
        _parser._actions = list()


def clear_parser():
    global _parser
    _parser = None


def get_parser(*args, **kwargs):
    global _parser
    if _parser is not None:
        if args or kwargs:
            logger.warning(f"Initialized parser already exists, but you ran {get_parser.__name__} with {args, kwargs}."
                           f"This func returned the existing parser, and your {args} and {kwargs} were ignored."
                           f"If you want new parser constructed with those arguments, "
                           f"call {clear_parser.__name__}() and then rerun initialize.")
    else:
        _parser = Parser(*args, **kwargs)
    return _parser


def set_parser(parser: Parser):
    global _parser
    _parser = parser


@init_parser
def parse(*args, is_flag=False, **kwargs):
    """alias of parser.parse"""
    return _parser.parse(*args, is_flag=is_flag, **kwargs)


@init_parser
def set_args_on_parse(*args, **kwargs):
    _parser.args_for_parse = args
    _parser.kwargs_on_parse = kwargs


def create_help(*args, **kwargs):
    """Create help and exit program. If help message by -h is not required, this method is not needed.
    Run this only when you want to show help by -h or --help"""
    if runs_with_help_option:
        _parser.parse_args(*args, **kwargs)
        exit()
    elif len(argv) == 1:
        logger.info("Script runs without arguments.")
        return


@init_parser
def subcommand(name, **kwargs):
    """Same arguments as ArgumentParser.subparsers.add_parser() are available."""
    global _parser
    assert isinstance(_parser, Parser)
    if not _parser.subparsers_list:
        _parser.add_subparsers()
    subparser = _parser.subparsers_action.add_parser(name, **kwargs)
    _parser.subparsers_list.append(subparser)
    try:
        namespace, _ = _parser.parse_known_args()
    except SubCommandNotFound:
        pass
    else:
        if hasattr(namespace, SUBCOMMAND):
            given_subcommand = getattr(namespace, SUBCOMMAND)
            if given_subcommand == name or given_subcommand is kwargs.get("dest", None):
                subparser.invoked = True
    return subparser


if __name__ == "__main__":

    def example():
        args = "--foo --fizz buzz sub2 --sub2opt bar"
        argv[1::] = args.split(" ")

        foo = parse("--foo", is_flag=True)
        print("--foo:", foo)

        fizz = parse("--fizz")
        print("--fizz:", fizz)

        if subcommand("sub1").invoked:
            print("sub1opt:", parse("--sub1opt"))
        elif subcommand("sub2").invoked:
            print("sub2opt:", parse("--sub2opt"))

    example()
