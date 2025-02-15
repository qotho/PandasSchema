import abc
import math
import datetime
import pandas as pd
import numpy as np
import typing
import operator

from . import column
from .validation_warning import ValidationWarning
from .errors import PanSchArgumentError
from pandas.api.types import is_categorical_dtype, is_numeric_dtype
from typing import List, Union


class _BaseValidation:
    """
    The validation base class that defines any object that can create a list of errors from a Series
    """
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def get_errors(self, series: pd.Series, column: 'column.Column') -> typing.Iterable[ValidationWarning]:
        """
        Return a list of errors in the given series
        :param series:
        :param column:
        :return:
        """


class _SeriesValidation(_BaseValidation):
    """
    Implements the _BaseValidation interface by returning a Boolean series for each element that either passes or
    fails the validation
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, **kwargs):
        self._custom_message = kwargs.get('message')

    @property
    def message(self):
        return self._custom_message or self.default_message

    @abc.abstractproperty
    def default_message(self) -> str:
        """
        Create a message to be displayed whenever this validation fails
        This should be a generic message for the validation type, but can be overwritten if the user provides a
        message kwarg
        """

    @abc.abstractmethod
    def validate(self, series: pd.Series) -> pd.Series:
        """
        Returns a Boolean series, where each value of False is an element in the Series that has failed the validation
        :param series:
        :return:
        """

    def __invert__(self):
        """
        Returns a negated version of this validation
        """
        return _InverseValidation(self)

    def __or__(self, other: '_SeriesValidation'):
        """
        Returns a validation which is true if either this or the other validation is true
        """
        return _CombinedValidation(self, other, operator.or_)

    def __and__(self, other: '_SeriesValidation'):
        """
        Returns a validation which is true if either this or the other validation is true
        """
        return _CombinedValidation(self, other, operator.and_)

    def get_errors(self, series: pd.Series, column: 'column.Column'):

        errors = []

        # Calculate which columns are valid using the child class's validate function, skipping empty entries if the
        # column specifies to do so
        simple_validation = ~self.validate(series)
        if column.allow_empty:
            # Failing results are those that are not empty, and fail the validation
            # explicitly check to make sure the series isn't a category because issubdtype will FAIL if it is
            if is_categorical_dtype(series) or is_numeric_dtype(series):
                validated = ~series.isnull() & simple_validation
            else:
                validated = (series.str.len() > 0) & simple_validation

        else:
            validated = simple_validation

        # Cut down the original series to only ones that failed the validation
        indices = series.index[validated]

        # Use these indices to find the failing items. Also print the index which is probably a row number
        for i in indices:
            element = series[i]
            errors.append(ValidationWarning(
                message=self.message,
                value=element,
                row=i,
                column=series.name
            ))

        return errors


class _InverseValidation(_SeriesValidation):
    """
    Negates an ElementValidation
    """

    def __init__(self, validation: _SeriesValidation):
        self.negated = validation
        super().__init__()

    def validate(self, series: pd.Series):
        return ~ self.negated.validate(series)

    @property
    def default_message(self):
        return self.negated.message + ' <negated>'


class _CombinedValidation(_SeriesValidation):
    """
    Validates if one and/or the other validation is true for an element
    """

    def __init__(self, validation_a: _SeriesValidation, validation_b: _SeriesValidation, operator):
        self.operator = operator
        self.v_a = validation_a
        self.v_b = validation_b
        super().__init__()

    def validate(self, series: pd.Series):
        return self.operator(self.v_a.validate(series), self.v_b.validate(series))

    @property
    def default_message(self):
        return '({}) {} ({})'.format(self.v_a.message, self.operator, self.v_b.message)


class CustomSeriesValidation(_SeriesValidation):
    """
    Validates using a user-provided function that operates on an entire series (for example by using one of the pandas
    Series methods: http://pandas.pydata.org/pandas-docs/stable/api.html#series)
    """

    def __init__(self, validation: typing.Callable[[pd.Series], pd.Series], message: str):
        """
        :param message: The error message to provide to the user if this validation fails. The row and column and
            failing value will automatically be prepended to this message, so you only have to provide a message that
            describes what went wrong, for example 'failed my validation' will become

            {row: 1, column: "Column Name"}: "Value" failed my validation
        :param validation: A function that takes a pandas Series and returns a boolean Series, where each cell is equal
            to True if the object passed validation, and False if it failed
        """
        self._validation = validation
        super().__init__(message=message)

    def validate(self, series: pd.Series) -> pd.Series:
        return self._validation(series)


class CustomElementValidation(_SeriesValidation):
    """
    Validates using a user-provided function that operates on each element
    """

    def __init__(self, validation: typing.Callable[[typing.Any], typing.Any], message: str):
        """
        :param message: The error message to provide to the user if this validation fails. The row and column and
            failing value will automatically be prepended to this message, so you only have to provide a message that
            describes what went wrong, for example 'failed my validation' will become

            {row: 1, column: "Column Name"}: "Value" failed my validation
        :param validation: A function that takes the value of a data frame cell and returns True if it passes the
            the validation, and false if it doesn't
        """
        self._validation = validation
        super().__init__(message=message)

    def validate(self, series: pd.Series) -> pd.Series:
        return series.apply(self._validation)


class InRangeValidation(_SeriesValidation):
    """
    Checks that each element in the series is within a given numerical range
    """

    def __init__(self, min: float = -math.inf, max: float = math.inf, **kwargs):
        """
        :param min: The minimum (inclusive) value to accept
        :param max: The maximum (exclusive) value to accept
        """
        self.min = min
        self.max = max
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return 'was not in the range [{}, {})'.format(self.min, self.max)

    def validate(self, series: pd.Series) -> pd.Series:
        series = pd.to_numeric(series, errors="coerce")
        return (series >= self.min) & (series < self.max)


class IsTypeValidation(_SeriesValidation):
    """
    Checks that each element in the series equals one of the allowed types. This validation only makes sense for an
    object series.
    Examples
    --------
    >>> v = IsTypeValidation(allowed_types=[str, int])
    >>> s = pd.Series(data=["alpha", 1.4, True, "beta", 5])
    >>> v.validate(series=s)
    0     True
    1    False
    2    False
    3     True
    4     True
    dtype: bool
    """

    def __init__(self, allowed_types: List, **kwargs):
        """
        :param allowed_types: List containing the allowed data types.
        """
        self.allowed_types = allowed_types
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return "is not of type listed in {}".format(self.allowed_types.__str__())

    def get_errors(self, series: pd.Series, column: 'column.Column'):

        # Numpy dtypes other than 'object' can be validated with IsDtypeValidation instead.
        if not series.dtype == np.dtype(object):
            np_allowed_types = [np.dtype(allowed_type) for allowed_type in self.allowed_types]
            alternative_validation_method = IsDtypeValidation(dtype=np_allowed_types)
            return alternative_validation_method.get_errors(series=series, column=column)

        return super().get_errors(series=series, column=column)

    def validate(self, series: pd.Series) -> pd.Series:
        return series.apply(type).isin(self.allowed_types)


class IsDtypeValidation(_BaseValidation):
    """
    Checks that a series has (one of) the required numpy dtype(s).
    Examples
    --------
    >>> v = IsDtypeValidation(dtype=[np.str0, np.float64])
    >>> s = pd.Series(data=np.array([1, 2, 3, 4, 5]), name='IntCol')
    >>> err = v.get_errors(series=s, column=Column(name=s.name.__str__()))
    >>> err[0].__str__()
    "The column IntCol has a dtype of int32 which is not a subclass of the required type [<class 'numpy.str_'>,
    <class 'numpy.float64'>]"
    """

    def __init__(self, dtype: Union[np.dtype, List[np.dtype]], **kwargs):
        """
        :param dtype: The numpy dtype to check the column against. Input can be either a single dtype or a list of
            dtypes.
        """
        self.dtype = dtype
        if type(dtype) is not list:
            self.dtype = [dtype]
        super().__init__(**kwargs)

    @staticmethod
    def numpy_dtype(dtype: Union[np.dtype, str]):
        if dtype.type == np.bool_:
            # BooleanDtype doesn't implement numpy_dtype in Pandas 1.0.5
            return np.bool
        elif is_extension_array_dtype(dtype):
            return dtype.numpy_dtype
        else:
            return dtype

    @staticmethod
    def convert_series_dtype_to_system_default(series: pd.Series) -> pd.Series:
        """ On Windows np.dtype(int) returns np.int32, whereas Pandas.Series([1, 2, 3, ..., n]).dtype returns np.int64.
        Linux does return np.int64 for np.dtype(int). Other types (float, bool, etc) return equal types.
        For this reason, the series is converted back and forth to ensure equal types between pandas and numpy."""

        series_dtype = IsDtypeValidation.numpy_dtype(series.dtype)

        # If not numeric, no conversion necessary
        if not np.issubdtype(series_dtype, np.number):
            return series

        # Convert
        if is_extension_array_dtype(series):
            return series.astype(series.dtype)
        else:
            python_type = type(np.zeros(1, series_dtype).tolist()[0])  # First convert to Python type.
            return series.astype(python_type)  # Then convert back based on system preference.


    def get_errors(self, series: pd.Series, column: 'column.Column' = None) -> list:

        # Convert to system dependent default numpy dtype.
        series_converted_type = self.convert_series_dtype_to_system_default(series=series)

        # Validate and return (possible) error messages
        if not self.validate(series=series_converted_type):
            return [ValidationWarning(
                'The column {} has a dtype of {} which is not a subclass of the required type {}'.format(
                    column.name if column else '', series_converted_type.dtype, self.dtype
                )
            )]
        else:
            return []

    def validate(self, series: pd.Series) -> bool:

        # Convert to system dependent default numpy dtype.
        series_converted_type = self.convert_series_dtype_to_system_default(series=series)

        return True in [np.issubdtype(self.numpy_dtype(series_converted_type.dtype), given_dtype) for given_dtype in self.dtype]


class CanCallValidation(_SeriesValidation):
    """
    Validates if a given function can be called on each element in a column without raising an exception
    """

    def __init__(self, func: typing.Callable, **kwargs):
        """
        :param func: A python function that will be called with the value of each cell in the DataFrame. If this
            function throws an error, this cell is considered to have failed the validation. Otherwise it has passed.
        """
        if callable(type):
            self.callable = func
        else:
            raise PanSchArgumentError('The object "{}" passed to CanCallValidation is not callable!'.format(type))
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return 'raised an exception when the callable {} was called on it'.format(self.callable)

    def can_call(self, var):
        try:
            self.callable(var)
            return True
        except:
            return False

    def validate(self, series: pd.Series) -> pd.Series:
        return series.apply(self.can_call)


class CanConvertValidation(CanCallValidation):
    """
    Checks if each element in a column can be converted to a Python object type
    """

    """
    Internally this uses the same logic as CanCallValidation since all types are callable in python.
    However this class overrides the error messages to make them more directed towards types
    """

    def __init__(self, _type: type, **kwargs):
        """
        :param _type: Any python type. Its constructor will be called with the value of the individual cell as its
            only argument. If it throws an exception, the value is considered to fail the validation, otherwise it has passed
        """
        if isinstance(_type, type):
            super(CanConvertValidation, self).__init__(_type, **kwargs)
        else:
            raise PanSchArgumentError('{} is not a valid type'.format(_type))

    @property
    def default_message(self):
        return 'cannot be converted to type {}'.format(self.callable)


class MatchesPatternValidation(_SeriesValidation):
    """
    Validates that a string or regular expression can match somewhere in each element in this column
    """

    def __init__(self, pattern, options={}, **kwargs):
        """
        :param kwargs: Arguments to pass to Series.str.contains
            (http://pandas.pydata.org/pandas-docs/stable/generated/pandas.Series.str.contains.html)
            pat is the only required argument
        """
        self.pattern = pattern
        self.options = options
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return 'does not match the pattern "{}"'.format(self.pattern)

    def validate(self, series: pd.Series) -> pd.Series:
        return series.astype(str).str.contains(self.pattern, **self.options)


class TrailingWhitespaceValidation(_SeriesValidation):
    """
    Checks that there is no trailing whitespace in this column
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return 'contains trailing whitespace'

    def validate(self, series: pd.Series) -> pd.Series:
        return ~series.astype(str).str.contains('\s+$')


class LeadingWhitespaceValidation(_SeriesValidation):
    """
    Checks that there is no leading whitespace in this column
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return 'contains leading whitespace'

    def validate(self, series: pd.Series) -> pd.Series:
        return ~series.astype(str).str.contains('^\s+')


class IsDistinctValidation(_SeriesValidation):
    """
    Checks that every element of this column is different from each other element
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return 'contains values that are not unique'

    def validate(self, series: pd.Series) -> pd.Series:
        return ~series.duplicated(keep='first')


class InListValidation(_SeriesValidation):
    """
    Checks that each element in this column is contained within a list of possibilities
    """

    def __init__(self, options: typing.Iterable, case_sensitive: bool = True, **kwargs):
        """
        :param options: A list of values to check. If the value of a cell is in this list, it is considered to pass the
            validation
        """
        self.case_sensitive = case_sensitive
        self.options = options
        super().__init__(**kwargs)

    @property
    def default_message(self):
        values = ', '.join(str(v) for v in self.options)
        return 'is not in the list of legal options ({})'.format(values)

    def validate(self, series: pd.Series) -> pd.Series:
        if self.case_sensitive:
            return series.isin(self.options)
        else:
            return series.str.lower().isin([s.lower() for s in self.options])


class DateFormatValidation(_SeriesValidation):
    """
    Checks that each element in this column is a valid date according to a provided format string
    """

    def __init__(self, date_format: str, **kwargs):
        """
        :param date_format: The date format string to validate the column against. Refer to the date format code
            documentation at https://docs.python.org/3/library/datetime.html#strftime-and-strptime-behavior for a full
            list of format codes
        """
        self.date_format = date_format
        super().__init__(**kwargs)

    @property
    def default_message(self):
        return 'does not match the date format string "{}"'.format(self.date_format)

    def valid_date(self, val):
        try:
            datetime.datetime.strptime(val, self.date_format)
            return True
        except:
            return False

    def validate(self, series: pd.Series) -> pd.Series:
        return series.astype(str).apply(self.valid_date)
