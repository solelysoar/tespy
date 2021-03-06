# -*- coding: utf-8

"""Module of class Separator.


This file is part of project TESPy (github.com/oemof/tespy). It's copyrighted
by the contributors recorded in the version control history of the file,
available from its original location tespy/components/nodes/separator.py

SPDX-License-Identifier: MIT
"""

import numpy as np

from tespy.components.component import Component
from tespy.components.nodes.node import Node
from tespy.tools.data_containers import DataContainerSimple as dc_simple
from tespy.tools.fluid_properties import T_mix_ph
from tespy.tools.fluid_properties import dT_mix_dph
from tespy.tools.fluid_properties import dT_mix_pdh
from tespy.tools.fluid_properties import dT_mix_ph_dfluid


class Separator(Node):
    r"""
    A separator separates fluid components from a mass flow.

    Equations

        **mandatory equations**

        - :py:meth:`tespy.components.component.Component.mass_flow_func`

        .. math::

            0 = p_{in} - p_{out,i} \;
            \forall i \in \mathrm{outlets}

        **additional equations**

        - :py:meth:`tespy.components.nodes.separator.Separator.additional_equations`

    Inlets/Outlets

        - in1
        - specify number of outlets with :code:`num_out` (default value: 2)

    Image

        .. image:: _images/Splitter.svg
           :alt: alternative text
           :align: center

    Note
    ----
    Fluid separation requires power and cooling, equations have not been
    implemented, yet!

    Parameters
    ----------
    label : str
        The label of the component.

    design : list
        List containing design parameters (stated as String).

    offdesign : list
        List containing offdesign parameters (stated as String).

    design_path : str
        Path to the components design case.

    local_offdesign : boolean
        Treat this component in offdesign mode in a design calculation.

    local_design : boolean
        Treat this component in design mode in an offdesign calculation.

    char_warnings : boolean
        Ignore warnings on default characteristics usage for this component.

    printout : boolean
        Include this component in the network's results printout.

    num_out : float, tespy.tools.data_containers.DataContainerSimple
        Number of outlets for this component, default value: 2.

    Example
    -------
    The separator is used to split up a single mass flow into a specified
    number of different parts at identical pressure and temperature but
    different fluid composition. Fluids can be separated from each other.

    >>> from tespy.components import Sink, Source, Separator
    >>> from tespy.connections import Connection
    >>> from tespy.networks import Network
    >>> import shutil
    >>> import numpy as np
    >>> fluid_list = ['O2', 'N2']
    >>> nw = Network(fluids=fluid_list, p_unit='bar', T_unit='C',
    ... iterinfo=False)
    >>> so = Source('source')
    >>> si1 = Sink('sink1')
    >>> si2 = Sink('sink2')
    >>> s = Separator('separator', num_out=2)
    >>> s.component()
    'separator'
    >>> inc = Connection(so, 'out1', s, 'in1')
    >>> outg1 = Connection(s, 'out1', si1, 'in1')
    >>> outg2 = Connection(s, 'out2', si2, 'in1')
    >>> nw.add_conns(inc, outg1, outg2)

    An Air (simplified) mass flow of 5 kg/s is split up into two mass flows.
    One mass flow of 1 kg/s containing 10 % oxygen and 90 % nitrogen leaves the
    separator. It is possible to calculate the fluid composition of the second
    mass flow. Specify starting values for the second mass flow fluid
    composition for calculation stability.

    >>> inc.set_attr(fluid={'O2': 0.23, 'N2': 0.77}, p=1, T=20, m=5)
    >>> outg1.set_attr(fluid={'O2': 0.1, 'N2': 0.9}, m=1)
    >>> outg2.set_attr(fluid0={'O2': 0.5, 'N2': 0.5})
    >>> nw.solve('design')
    >>> outg2.fluid.val['O2']
    0.2625

    In the same way, it is possible to specify one of the fluid components in
    the second mass flow instead of the first mass flow. The solver will find
    the mass flows matching the desired composition. 65 % of the mass flow
    will leave the separator at the second outlet the case of 30 % oxygen
    mass fraction for this outlet.

    >>> outg1.set_attr(m=np.nan)
    >>> outg2.set_attr(fluid={'O2': 0.3})
    >>> nw.solve('design')
    >>> outg2.fluid.val['O2']
    0.3
    >>> round(outg2.m.val_SI / inc.m.val_SI, 2)
    0.65
    """

    @staticmethod
    def component():
        return 'separator'

    @staticmethod
    def attr():
        return {'num_out': dc_simple()}

    @staticmethod
    def inlets():
        return ['in1']

    def outlets(self):
        if self.num_out.is_set:
            return ['out' + str(i + 1) for i in range(self.num_out.val)]
        else:
            self.set_attr(num_out=2)
            return self.outlets()

    def comp_init(self, nw):

        Component.comp_init(self, nw)

        # number of mandatroy equations for
        # mass flow: 1
        # pressure: number of inlets + number of outlets - 1
        # fluid: number of fluids
        # enthalpy: number of outlets

        self.num_eq = self.num_i + self.num_o * 2 + self.num_nw_fluids

        self.jacobian = np.zeros((
            self.num_eq,
            self.num_i + self.num_o + self.num_vars,
            self.num_nw_vars))

        self.residual = np.zeros(self.num_eq)
        self.jacobian[0:1] = self.mass_flow_deriv()
        end = self.num_i + self.num_o
        self.jacobian[1:end] = self.pressure_deriv()

    def additional_equations(self, k):
        r"""
        Calculate results of additional equations.

        Equations

            **mandatroy equations**

            .. math:: 0 = fluid_{i,in} - fluid_{i,out_{j}} \;
                \forall i \in \mathrm{fluid}, \; \forall j \in outlets

            .. math::

                0 = T_{in} - T_{out,i} \;
                \forall i \in \mathrm{outlets}
        """
        ######################################################################
        # equations for fluid balance
        for fluid, x in self.inl[0].fluid.val.items():
            res = x * self.inl[0].m.val_SI
            for o in self.outl:
                res -= o.fluid.val[fluid] * o.m.val_SI
            self.residual[k] = res
            k += 1

        ######################################################################
        # equations for energy balance
        for o in self.outl:
            self.residual[k] = (
                T_mix_ph(self.inl[0].to_flow(), T0=self.inl[0].T.val_SI) -
                T_mix_ph(o.to_flow(), T0=o.T.val_SI))
            k += 1

    def additional_derivatives(self, increment_filter, k):
        r"""Calculate partial derivatives for given additional equations."""
        ######################################################################
        # derivatives for fluid balance equations
        i = 0
        for fluid in self.nw_fluids:
            j = 0
            for o in self.outl:
                self.jacobian[k, j + 1, 0] = -o.fluid.val[fluid]
                self.jacobian[k, j + 1, i + 3] = -o.m.val_SI
                j += 1
            self.jacobian[k, 0, 0] = self.inl[0].fluid.val[fluid]
            self.jacobian[k, 0, i + 3] = self.inl[0].m.val_SI
            k += 1
            i += 1

        ######################################################################
        # derivatives for energy balance equations
        i = self.inl[0].to_flow()
        j = 0
        for o in self.outl:
            o = o.to_flow()
            self.jacobian[k, 0, 1] = dT_mix_dph(i)
            self.jacobian[k, 0, 2] = dT_mix_pdh(i)
            self.jacobian[k, 0, 3:] = dT_mix_ph_dfluid(i)
            self.jacobian[k, j + 1, 1] = -dT_mix_dph(o)
            self.jacobian[k, j + 1, 2] = -dT_mix_pdh(o)
            self.jacobian[k, j + 1, 3:] = -1 * dT_mix_ph_dfluid(o)
            j += 1
            k += 1

    def initialise_fluids(self):
        """Overwrite parent method."""
        return

    def entropy_balance(self):
        r"""Entropy balance calculation method."""
        return

    def exergy_balance(self, T0):
        r"""
        Exergy balance calculation method.

        Parameters
        ----------
        T0 : float
            Ambient temperature T0 / K.
        """
        Component.exergy_balance(self, T0)

    def get_plotting_data(self):
        return
