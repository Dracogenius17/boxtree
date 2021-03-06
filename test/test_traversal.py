from __future__ import division, absolute_import

__copyright__ = "Copyright (C) 2013 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


from six.moves import range

import numpy as np
import numpy.linalg as la
import pyopencl as cl

import pytest
from pyopencl.tools import (  # noqa
        pytest_generate_tests_for_pyopencl as pytest_generate_tests)

from boxtree.tools import make_normal_particle_array

import logging
logger = logging.getLogger(__name__)


# {{{ connectivity test

@pytest.mark.opencl
@pytest.mark.parametrize(("dims", "sources_are_targets"), [
    (2, True),
    (2, False),
    (3, True),
    (3, False),
    ])
def test_tree_connectivity(ctx_getter, dims, sources_are_targets):
    logging.basicConfig(level=logging.INFO)

    ctx = ctx_getter()
    queue = cl.CommandQueue(ctx)

    dtype = np.float64

    sources = make_normal_particle_array(queue, 1 * 10**5, dims, dtype)
    if sources_are_targets:
        targets = None
    else:
        targets = make_normal_particle_array(queue, 2 * 10**5, dims, dtype)

    from boxtree import TreeBuilder
    tb = TreeBuilder(ctx)
    tree, _ = tb(queue, sources, max_particles_in_box=30,
            targets=targets, debug=True)

    from boxtree.traversal import FMMTraversalBuilder
    tg = FMMTraversalBuilder(ctx)
    trav, _ = tg(queue, tree, debug=True)
    tree = tree.get(queue=queue)
    trav = trav.get(queue=queue)

    levels = tree.box_levels
    parents = tree.box_parent_ids.T
    children = tree.box_child_ids.T
    centers = tree.box_centers.T

    # {{{ parent and child relations, levels match up

    for ibox in range(1, tree.nboxes):
        # /!\ Not testing box 0, has no parents
        parent = parents[ibox]

        assert levels[parent] + 1 == levels[ibox]
        assert ibox in children[parent], ibox

    # }}}

    if 0:
        import matplotlib.pyplot as pt
        from boxtree.visualization import TreePlotter
        plotter = TreePlotter(tree)
        plotter.draw_tree(fill=False, edgecolor="black")
        plotter.draw_box_numbers()
        plotter.set_bounding_box()
        pt.show()

    # {{{ neighbor_source_boxes (list 1) consists of source boxes

    for itgt_box, ibox in enumerate(trav.target_boxes):
        start, end = trav.neighbor_source_boxes_starts[itgt_box:itgt_box+2]
        nbl = trav.neighbor_source_boxes_lists[start:end]

        if sources_are_targets:
            assert ibox in nbl

        for jbox in nbl:
            assert (0 == children[jbox]).all(), (ibox, jbox, children[jbox])

    logger.info("list 1 consists of source boxes")

    # }}}

    # {{{ separated siblings (list 2) are actually separated

    for itgt_box, tgt_ibox in enumerate(trav.target_or_target_parent_boxes):
        start, end = trav.from_sep_siblings_starts[itgt_box:itgt_box+2]
        seps = trav.from_sep_siblings_lists[start:end]

        assert (levels[seps] == levels[tgt_ibox]).all()

        # three-ish box radii (half of size)
        mindist = 2.5 * 0.5 * 2**-int(levels[tgt_ibox]) * tree.root_extent

        icenter = centers[tgt_ibox]
        for jbox in seps:
            dist = la.norm(centers[jbox]-icenter)
            assert dist > mindist, (dist, mindist)

    logger.info("separated siblings (list 2) are actually separated")

    # }}}

    if sources_are_targets:
        # {{{ from_sep_{smaller,bigger} are duals of each other

        assert (trav.target_or_target_parent_boxes == np.arange(tree.nboxes)).all()

        # {{{ list 4 <= list 3
        for itarget_box, ibox in enumerate(trav.target_boxes):

            for ssn in trav.from_sep_smaller_by_level:
                start, end = ssn.starts[itarget_box:itarget_box+2]

                for jbox in ssn.lists[start:end]:
                    rstart, rend = trav.from_sep_bigger_starts[jbox:jbox+2]

                    assert ibox in trav.from_sep_bigger_lists[rstart:rend], \
                            (ibox, jbox)

        # }}}

        # {{{ list 4 <= list 3

        box_to_target_box_index = np.empty(tree.nboxes, tree.box_id_dtype)
        box_to_target_box_index.fill(-1)
        box_to_target_box_index[trav.target_boxes] = np.arange(
                len(trav.target_boxes), dtype=tree.box_id_dtype)

        assert (trav.source_boxes == trav.target_boxes).all()
        assert (trav.target_or_target_parent_boxes == np.arange(
                tree.nboxes, dtype=tree.box_id_dtype)).all()

        for ibox in range(tree.nboxes):
            start, end = trav.from_sep_bigger_starts[ibox:ibox+2]

            for jbox in trav.from_sep_bigger_lists[start:end]:
                # In principle, entries of from_sep_bigger_lists are
                # source boxes. In this special case, source and target boxes
                # are the same thing (i.e. leaves--see assertion above), so we
                # may treat them as targets anyhow.

                jtgt_box = box_to_target_box_index[jbox]
                assert jtgt_box != -1

                good = False

                for ssn in trav.from_sep_smaller_by_level:
                    rstart, rend = ssn.starts[jtgt_box:jtgt_box+2]
                    good = good or ibox in ssn.lists[rstart:rend]

                if not good:
                    from boxtree.visualization import TreePlotter
                    plotter = TreePlotter(tree)
                    plotter.draw_tree(fill=False, edgecolor="black", zorder=10)
                    plotter.set_bounding_box()

                    plotter.draw_box(ibox, facecolor='green', alpha=0.5)
                    plotter.draw_box(jbox, facecolor='red', alpha=0.5)

                    import matplotlib.pyplot as pt
                    pt.gca().set_aspect("equal")
                    pt.show()

                # This assertion failing means that ibox's list 4 contains a box
                # 'jbox' whose list 3 does not contain ibox.
                assert good, (ibox, jbox)

        # }}}

        logger.info("list 3, 4 are duals")

        # }}}

    # {{{ from_sep_smaller satisfies relative level assumption

    for itarget_box, ibox in enumerate(trav.target_boxes):
        for ssn in trav.from_sep_smaller_by_level:
            start, end = ssn.starts[itarget_box:itarget_box+2]

            for jbox in ssn.lists[start:end]:
                assert levels[ibox] < levels[jbox]

    logger.info("list 3 satisfies relative level assumption")

    # }}}

    # {{{ from_sep_bigger satisfies relative level assumption

    for itgt_box, tgt_ibox in enumerate(trav.target_or_target_parent_boxes):
        start, end = trav.from_sep_bigger_starts[itgt_box:itgt_box+2]

        for jbox in trav.from_sep_bigger_lists[start:end]:
            assert levels[tgt_ibox] > levels[jbox]

    logger.info("list 4 satisfies relative level assumption")

    # }}}

    # {{{ level_start_*_box_nrs lists make sense

    for name, ref_array in [
            ("level_start_source_box_nrs", trav.source_boxes),
            ("level_start_source_parent_box_nrs", trav.source_parent_boxes),
            ("level_start_target_box_nrs", trav.target_boxes),
            ("level_start_target_or_target_parent_box_nrs",
                trav.target_or_target_parent_boxes)
            ]:
        level_starts = getattr(trav, name)
        for lev in range(tree.nlevels):
            start, stop = level_starts[lev:lev+2]

            box_nrs = ref_array[start:stop]

            assert (tree.box_levels[box_nrs] == lev).all(), name

    # }}}

    # {{{ box extents make sense

    for ibox in range(tree.nboxes):
        ext_low, ext_high = tree.get_box_extent(ibox)
        center = tree.box_centers[:, ibox]

        for which, bbox_min, bbox_max in [
                (
                    "source",
                    trav.box_source_bounding_box_min[:, ibox],
                    trav.box_source_bounding_box_max[:, ibox]),
                (
                    "target",
                    trav.box_target_bounding_box_min[:, ibox],
                    trav.box_target_bounding_box_max[:, ibox]),
                ]:
            assert (ext_low <= bbox_min).all()
            assert (bbox_min <= center).all()

            assert (bbox_max <= ext_high).all()
            assert (center <= bbox_max).all()

    # }}}

# }}}


# {{{ visualization helper (not a test)

def plot_traversal(ctx_getter, do_plot=False, well_sep_is_n_away=1):
    ctx = ctx_getter()
    queue = cl.CommandQueue(ctx)

    #for dims in [2, 3]:
    for dims in [2]:
        nparticles = 10**4
        dtype = np.float64

        from pyopencl.clrandom import PhiloxGenerator
        rng = PhiloxGenerator(queue.context, seed=15)

        from pytools.obj_array import make_obj_array
        particles = make_obj_array([
            rng.normal(queue, nparticles, dtype=dtype)
            for i in range(dims)])

        # if do_plot:
        #     pt.plot(particles[0].get(), particles[1].get(), "x")

        from boxtree import TreeBuilder
        tb = TreeBuilder(ctx)

        queue.finish()
        tree, _ = tb(queue, particles, max_particles_in_box=30, debug=True)

        from boxtree.traversal import FMMTraversalBuilder
        tg = FMMTraversalBuilder(ctx, well_sep_is_n_away=well_sep_is_n_away)
        trav, _ = tg(queue, tree)

        tree = tree.get(queue=queue)
        trav = trav.get(queue=queue)

        from boxtree.visualization import TreePlotter
        plotter = TreePlotter(tree)
        plotter.draw_tree(fill=False, edgecolor="black")
        #plotter.draw_box_numbers()
        plotter.set_bounding_box()

        from random import randrange, seed  # noqa
        seed(7)

        from boxtree.visualization import draw_box_lists

        #draw_box_lists(randrange(tree.nboxes))
        draw_box_lists(plotter, trav, 320)
        #plotter.draw_box_numbers()

        import matplotlib.pyplot as pt
        pt.show()

# }}}


# You can test individual routines by typing
# $ python test_traversal.py 'test_routine(cl.create_some_context)'

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from py.test.cmdline import main
        main([__file__])

# vim: fdm=marker
