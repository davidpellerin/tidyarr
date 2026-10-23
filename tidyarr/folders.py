"""Which folders the web UI may read from and write to.

The two roots come from server-side configuration (the settings file, config.py, or the command line), never from the browser,
which only shows them. A scan always reads the whole input root and moves files under the output root.
"""
import os
import shutil
from pathlib import Path

from tidyarr import config


def inside(path, root):
    return path == root or root in path.parents


def move_file(source, target):
    """Move one file, never replacing anything already at `target`. Within one filesystem it is a hard link then an
    unlink, so the name is claimed atomically; across filesystems the bytes are copied into a newly created file first."""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)  # fails if target exists
    except FileExistsError:
        raise ValueError("a file with that name is already there") from None
    except OSError:  # another filesystem, or one without hard links
        try:
            with open(source, "rb") as reader, open(target, "xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
        except FileExistsError:
            raise ValueError("a file with that name is already there") from None
        except BaseException:
            target.unlink(missing_ok=True)  # never leave half a copy behind
            raise
        shutil.copystat(source, target)
    source.unlink()


def prune_empty(folder, stop):
    """Remove `folder` and its parents while they are empty, up to (not including) `stop`."""
    folder, stop = Path(folder), Path(stop)
    while folder != stop and stop in folder.parents:
        try:
            folder.rmdir()  # only ever removes an empty folder
        except OSError:
            return
        folder = folder.parent


class Roots:
    def __init__(self, input_root, output_root):
        self.input_root, self.output_root = (self._root(r) for r in (input_root, output_root))
        if inside(self.input_root, self.output_root) or inside(self.output_root, self.input_root):
            raise ValueError("the input and output roots must not contain one another")

    @staticmethod
    def _root(path):
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"not a folder: {root} (create it, or set input_root/output_root in {config.PATH} or with --input-root/--output-root)")
        return root

    @staticmethod
    def shown(root):  # also used for the output folder in the "new path" line
        """The root as the person would type it: ~/Downloads, not /home/you/Downloads. For display only."""
        home = Path.home()
        return "~" if root == home else f"~/{root.relative_to(home).as_posix()}" if home in root.parents else str(root)

    def choices(self):
        return {"input": self.shown(self.input_root), "output": self.shown(self.output_root)}
