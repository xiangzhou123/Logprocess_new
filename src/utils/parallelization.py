import contextlib
from typing import List, Union

import joblib
import pandas as pd
from tqdm.auto import tqdm


def parallelizeFunction(
    func,
    data: Union[pd.Series, List],
    workers=-1,
    prefer="processes",
    output: str = "list",
    show_progress: bool = True,
    desc: str = "",
    leave: bool = False,
    position: int = 0,
    *args,
    **kwargs,
):
    """
    Parallelizes function execution

    Parameters:
    -----------
    func:
        Function to be parallelized
    data:
        Data to be processed
    workers: int
        Number of worker processes. Default -1 (all cores)
    prefer: str
        Preferred backend for parallelization ("processes" or "threads")
    output:
        Type of output ("series" or "list")
    show_progress: bool
        If `True` a progress bar is shown.
    desc: str
        Description for the progress bar.
    leave: bool
        If `True` keeps all traces of the progress bar.
    position: int
        Specify the line offset to print this bar (starting from 0)
    args, kwargs:
        Additional arguments for the function

    Returns:
    --------
    Processed results as a Pandas Series or List
    """

    if show_progress:
        with tqdmJoblib(
            tqdm(desc=desc, total=len(data), leave=leave, position=position)
        ) as progress_bar:
            results = joblib.Parallel(n_jobs=workers, prefer=prefer, verbose=0)(
                joblib.delayed(func)(x, *args, **kwargs) for x in data
            )
    else:
        results = joblib.Parallel(n_jobs=workers, prefer=prefer, verbose=0)(
            joblib.delayed(func)(x, *args, **kwargs) for x in data
        )

    results = list(results)
    if output == "series" and isinstance(data, pd.Series):
        results = pd.Series(results, index=data.index)
        return results
    if results is None:
        return []
    return results


@contextlib.contextmanager
def tqdmJoblib(tqdm_object):
    """Context manager to patch joblib to report into tqdm progress bar given as argument"""

    class TqdmBatchCompletionCallback(joblib.parallel.BatchCompletionCallBack):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._tqdm_object = tqdm_object

        def __call__(self, *args, **kwargs):
            # Check if the callback has `batch_size` attribute
            if hasattr(self, "batch_size"):
                self._tqdm_object.update(n=self.batch_size)
            return super().__call__(*args, **kwargs)

    old_batch_callback = joblib.parallel.BatchCompletionCallBack
    joblib.parallel.BatchCompletionCallBack = TqdmBatchCompletionCallback
    try:
        yield tqdm_object
    finally:
        joblib.parallel.BatchCompletionCallBack = old_batch_callback
        tqdm_object.close()
