"""Resource axes expose useful, distinct labels without altering observations."""
import math
from fractions import Fraction
from decimal import Decimal

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pytest

from research_env.analysis import compute, value
from research_env.analysis_charts import STYLE, _axis_number, _resource_axis, figures
from test_analysis import filled_snapshot


@pytest.mark.parametrize('number,step,label', [
    (870000, 10000, '870.000'),
    (1234567, 1, '1.234.567'),
    (.1125, .0025, '0,1125'),
    (.0000025, .0000005, '0,0000025'),
    (.015, .005, '0,015'),
    (-1e-15, .1, '0,0'),
    (-1234.5, .5, '-1.234,5'),
])
def test_tick_labels_keep_decimal_precision_and_group_digits(number, step, label):
    assert _axis_number(number, step) == label


@pytest.mark.parametrize('limits,integer,min_labels', [
    ((243.608279145, 696.074946285), False, 4),
    ((850754.88, 890002.12), True, 4),
    ((1111630, 1210943), True, 4),
    ((.11049462, .12222998), False, 4),
    ((.000001, .000009), False, 4),
    ((0, .00012), True, 3),
    ((.999, 1.001), True, 3),
    ((0, 1), True, 3),
    ((0, 24), True, 4),
])
def test_resource_ticks_are_distinct_finite_and_count_ticks_are_integral(limits, integer, min_labels):
    with matplotlib.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(2.3, 3.8))
        try:
            ax.set_xlim(*limits)
            _resource_axis(ax, integer=integer)
            fig.canvas.draw()
            low, high = ax.get_xlim()
            assert math.isfinite(low) and low < high and math.isfinite(high)
            ticks = [(v, text.get_text()) for v, text in zip(ax.get_xticks(), ax.get_xticklabels()) if low <= v <= high]
            assert len(ticks) >= min_labels
            assert len(set(label for _, label in ticks)) == len(ticks)
            assert all('e' not in label for _, label in ticks)
            assert ax.xaxis.get_offset_text().get_text() == ''
            if integer:
                assert all(v == round(v) for v, _ in ticks)
                assert all(v == round(v) for v in ax.xaxis.get_minorticklocs())
        finally:
            plt.close(fig)


@pytest.mark.parametrize('cost_scale', [1, 100000])
def test_three_panel_resource_axes_remain_shared_and_labels_do_not_overlap(cost_scale):
    snapshot = filled_snapshot()
    for index, row in enumerate(snapshot['rows']):
        row['resources'] = {
            'pipeline_seconds': value(str(287 + index * 12), unit='s'),
            'tokens': {'summary': {'total': {'status':'complete', 'value':str(854553 + index*1000)}}},
            'costs': {'status':'known','amount':str(Decimal(1116+index*3)/Decimal(10000*cost_scale)), 'currency':'USD'},
        }
    result = compute(snapshot)
    for name, fig, info in figures(result):
        try:
            if not name.startswith('F-'):
                continue
            fig.set_dpi(180)
            fig.canvas.draw()
            assert len(set(ax.get_xlim() for ax in fig.axes)) == 1
            assert len(set(tuple(ax.get_xticks()) for ax in fig.axes)) == 1
            for ax in fig.axes:
                assert list(ax.get_yticks()) == [i/6 for i in range(7)]
                low, high = ax.get_xlim()
                labels = [label for v,label in zip(ax.get_xticks(),ax.get_xticklabels()) if low<=v<=high]
                boxes = [label.get_window_extent(fig.canvas.get_renderer()) for label in labels]
                assert len(labels)>=4, (name,[x.get_text() for x in labels])
                assert all(b.x0-a.x1 >= 5 for a,b in zip(boxes,boxes[1:])), name
                if name=='F-tokens':
                    assert ax.get_xlabel()=='Tokens gesamt\n(in Tausend)'
                    assert all(len(label.get_text())<=4 for label in labels)
                assert len(ax.xaxis.get_minorticklocs())>0
            lookup={r['id']:r for r in result['cells']}
            for point in info['figure_data']['points']:
                resources=lookup[point['planned_id']]['resources']
                if name=='F-zeit':original=resources['pipeline_seconds']['value']
                elif name=='F-tokens':original=resources['tokens']['summary']['total']['value']
                else:original=resources['costs']['amount']
                assert Fraction(point['x_value'])==Fraction(original)
        finally:
            plt.close(fig)
