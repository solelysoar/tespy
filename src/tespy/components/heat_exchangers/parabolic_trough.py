# -*- coding: utf-8

"""Module of class ParabolicTrough.


This file is part of project TESPy (github.com/oemof/tespy). It's copyrighted
by the contributors recorded in the version control history of the file,
available from its original location
tespy/components/heat_exchangers/parabolic_trough.py

SPDX-License-Identifier: MIT
"""

import logging

import numpy as np

from tespy.components.component import Component
from tespy.components.heat_exchangers.heat_exchanger_simple import HeatExchangerSimple
from tespy.tools.data_containers import ComponentProperties as dc_cp
from tespy.tools.data_containers import DataContainerSimple as dc_simple
from tespy.tools.data_containers import GroupedComponentProperties as dc_gcp
from tespy.tools.fluid_properties import T_mix_ph
from tespy.tools.global_vars import err


class ParabolicTrough(HeatExchangerSimple):
    r"""
    The ParabolicTrough calculates heat output from irradiance.

    Equations

        **mandatory equations**

        - :py:meth:`tespy.components.component.Component.fluid_func`
        - :py:meth:`tespy.components.component.Component.mass_flow_func`

        **optional equations**

        .. math::

            0 = \dot{m}_{in} \cdot \left(h_{out} - h_{in} \right) -
            \dot{Q}

            0 = p_{in} \cdot pr - p_{out}

        - :py:meth:`tespy.components.component.Component.zeta_func`

        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger_simple.HeatExchangerSimple.darcy_func`
          or :py:meth:`tespy.components.heat_exchangers.heat_exchanger_simple.HeatExchangerSimple.hw_func`

        **additional equations**

        - :py:meth:`tespy.components.heat_exchangers.parabolic_trough.ParabolicTrough.additional_equations`

    Inlets/Outlets

        - in1
        - out1

    Image

        .. image:: _images/ParabolicTrough.svg
           :alt: alternative text
           :align: center

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

    Q : str, float, tespy.tools.data_containers.ComponentProperties
        Heat transfer, :math:`Q/\text{W}`.

    pr : str, float, tespy.tools.data_containers.ComponentProperties
        Outlet to inlet pressure ratio, :math:`pr/1`.

    zeta : str, float, tespy.tools.data_containers.ComponentProperties
        Geometry independent friction coefficient,
        :math:`\frac{\zeta}{D^4}/\frac{1}{\text{m}^4}`.

    D : str, float, tespy.tools.data_containers.ComponentProperties
        Diameter of the absorber tube, :math:`D/\text{m}`.

    L : str, float, tespy.tools.data_containers.ComponentProperties
        Length of the absorber tube, :math:`L/\text{m}`.

    ks : str, float, tespy.tools.data_containers.ComponentProperties
        Tube's roughness, :math:`ks/\text{m}` for darcy friction,
        :math:`ks/\text{1}` for hazen-williams equation.

    hydro_group : str, tespy.tools.data_containers.GroupedComponentProperties
        Parametergroup for pressure drop calculation based on pipes dimensions.
        Choose 'HW' for hazen-williams equation, else darcy friction factor is
        used.

    E : str, float, tespy.tools.data_containers.ComponentProperties
        Direct irradiance to tilted collector,
        :math:`E/\frac{\text{W}}{\text{m}^2}`.

    aoi : str, float, tespy.tools.data_containers.ComponentProperties
        Angle of incidience, :math:`aoi/^\circ`.

    doc : str, float, tespy.tools.data_containers.ComponentProperties
        Degree of cleanliness (1: full absorption, 0: no absorption),
        :math:`X`.

    eta_opt : str, float, tespy.tools.data_containers.ComponentProperties
        (constant) optical losses due to surface reflection,
        :math:`\eta_{opt}`.

    c_1 : str, float, tespy.tools.data_containers.ComponentProperties
        Linear thermal loss key figure,
        :math:`c_1/\frac{\text{W}}{\text{K} \cdot \text{m}^2}`.

    c_2 : str, float, tespy.tools.data_containers.ComponentProperties
        Quadratic thermal loss key figure,
        :math:`c_2/\frac{\text{W}}{\text{K}^2 \cdot \text{m}^2}`.

    iam_1 : str, float, tespy.tools.data_containers.ComponentProperties
        Linear incidence angle modifier,
        :math:`iam_1/\frac{1}{^\circ}`.

    iam_2 : str, float, tespy.tools.data_containers.ComponentProperties
        Quadratic incidence angle modifier,
        :math:`iam_2/\left(\frac{1}{^\circ}\right)^2`.

    A : str, float, tespy.tools.data_containers.ComponentProperties
        Collector aperture surface area :math:`A/\text{m}^2`.

    Tamb : float, tespy.tools.data_containers.DataContainerSimple
        Ambient temperature, provide parameter in network's temperature unit.

    energy_group : tespy.tools.data_containers.GroupedComponentProperties
        Parametergroup for energy balance of solarthermal collector.

    Example
    -------
    A parabolic trough is installed using S800 as thermo-fluid.
    First, the operation conditions from :cite:`Janotte2014` are reproduced.
    Therefore, the direct normal irradiance :math:`\dot{E}_\mathrm{DNI}` is at
    1000 :math:`\frac{\text{W}}{\text{m}^2}` at an angle of incidence
    :math:`aoi` at 20 °. This means, the direct irradiance to the parabolic
    trough :math:`E` is at
    :math:`\dot{E}_{DNI} \cdot cos\left(20^\circ\right)`.

    >>> from tespy.components import Sink, Source, ParabolicTrough
    >>> from tespy.connections import Connection
    >>> from tespy.networks import Network
    >>> import numpy as np
    >>> import shutil
    >>> fluids = ['INCOMP::S800']
    >>> nw = Network(fluids=fluids)
    >>> nw.set_attr(p_unit='bar', T_unit='C', h_unit='kJ / kg', iterinfo=False)
    >>> so = Source('source')
    >>> si = Sink('sink')
    >>> pt = ParabolicTrough('parabolic trough collector')
    >>> pt.component()
    'parabolic trough'
    >>> inc = Connection(so, 'out1', pt, 'in1')
    >>> outg = Connection(pt, 'out1', si, 'in1')
    >>> nw.add_conns(inc, outg)

    The pressure ratio is at a constant level of 1. However, it is possible to
    specify the pressure losses from the absorber tube length, roughness and
    diameter, too. The aperture surface :math:`A` is specified to 1
    :math:`\text{m}^2` for simplicity reasons.

    >>> aoi = 20
    >>> E = 1000 * np.cos(aoi / 180 * np.pi)
    >>> pt.set_attr(pr=1, aoi=aoi, doc=1,
    ... Tamb=20, A=1, eta_opt=0.816, c_1=0.0622, c_2=0.00023, E=E,
    ... iam_1=-1.59e-3, iam_2=9.77e-5)
    >>> inc.set_attr(fluid={'S800': 1}, T=220, p=2)
    >>> outg.set_attr(T=260)
    >>> nw.solve('design')
    >>> round(pt.Q.val, 0)
    736.0

    For example, it is possible to calculate the aperture area of the parabolic
    trough given the total heat production, outflow temperature and mass flow.

    >>> pt.set_attr(A='var', Q=5e6, Tamb=25)
    >>> inc.set_attr(T=None)
    >>> outg.set_attr(T=350, m=20)
    >>> nw.solve('design')
    >>> round(inc.T.val, 0)
    229.0
    >>> round(pt.A.val, 0)
    6862.0

    Given this design, it is possible to calculate the outlet temperature as
    well as the heat transfer at different operating points.

    >>> aoi = 30
    >>> E = 800 * np.cos(aoi / 180 * np.pi)
    >>> pt.set_attr(A=pt.A.val, aoi=aoi, Q=None, E=E)
    >>> inc.set_attr(T=150)
    >>> outg.set_attr(T=None)
    >>> nw.solve('design')
    >>> round(outg.T.val, 0)
    244.0
    >>> round(pt.Q.val, 0)
    3603027.0
    """

    @staticmethod
    def component():
        return 'parabolic trough'

    @staticmethod
    def attr():
        return {
            'Q': dc_cp(),
            'pr': dc_cp(min_val=1e-4, max_val=1), 'zeta': dc_cp(min_val=0),
            'D': dc_cp(min_val=1e-2, max_val=2, d=1e-4),
            'L': dc_cp(min_val=1e-1, d=1e-3),
            'ks': dc_cp(val=1e-4, min_val=1e-7, max_val=1e-4, d=1e-8),
            'E': dc_cp(min_val=0), 'A': dc_cp(min_val=0),
            'eta_opt': dc_cp(min_val=0, max_val=1),
            'c_1': dc_cp(min_val=0), 'c_2': dc_cp(min_val=0),
            'iam_1': dc_cp(), 'iam_2': dc_cp(),
            'aoi': dc_cp(min_val=-90, max_val=90),
            'doc': dc_cp(min_val=0, max_val=1),
            'Tamb': dc_simple(),
            'Q_loss': dc_cp(min_val=0),
            'dissipative': dc_simple(val=True),
            'hydro_group': dc_gcp(), 'energy_group': dc_gcp()
        }

    def comp_init(self, nw):

        Component.comp_init(self, nw)

        self.Tamb.val_SI = ((self.Tamb.val + nw.T[nw.T_unit][0]) *
                            nw.T[nw.T_unit][1])

        # parameters for hydro group
        self.hydro_group.set_attr(elements=[self.L, self.ks, self.D])

        is_set = True
        for e in self.hydro_group.elements:
            if not e.is_set:
                is_set = False

        if is_set:
            self.hydro_group.set_attr(is_set=True)
        elif self.hydro_group.is_set is True:
            msg = (
                'All parameters of the component group have to be specified! '
                'This component group uses the following parameters: L, ks, D '
                'at ' + self.label + '. Group will be set to False.')
            logging.warning(msg)
            self.hydro_group.set_attr(is_set=False)
        else:
            self.hydro_group.set_attr(is_set=False)

        # parameters for energy group
        self.energy_group.set_attr(
            elements=[
                self.E, self.eta_opt, self.aoi, self.doc, self.c_1, self.c_2,
                self.iam_1, self.iam_2, self.A, self.Tamb])

        is_set = True
        for e in self.energy_group.elements:
            if not e.is_set:
                is_set = False

        if is_set:
            self.energy_group.set_attr(is_set=True)
        elif self.energy_group.is_set is True:
            msg = (
                'All parameters of the component group have to be specified! '
                'This component group uses the following parameters: E, '
                'eta_opt, aoi, doc, c_1, c_2, iam_1, iam_2, A, Tamb at ' +
                self.label + '. Group will be set to False.')
            logging.warning(msg)
            self.energy_group.set_attr(is_set=False)
        else:
            self.energy_group.set_attr(is_set=False)

        # number of mandatroy equations for
        # fluid balance: num_fl
        # mass flow: 1
        self.num_eq = self.num_nw_fluids + 1
        for var in [self.Q, self.pr, self.zeta, self.hydro_group,
                    self.energy_group]:
            if var.is_set is True:
                self.num_eq += 1

        self.jacobian = np.zeros((
            self.num_eq,
            self.num_i + self.num_o + self.num_vars,
            self.num_nw_vars))

        self.residual = np.zeros(self.num_eq)
        pos = self.num_nw_fluids
        self.jacobian[0:pos] = self.fluid_deriv()
        self.jacobian[pos:pos + 1] = self.mass_flow_deriv()

    def additional_equations(self, k):
        r"""
        Calculate results of additional equations.

        Equations

            **optional equations**

            - :py:meth:`tespy.components.heat_exchangers.parabolic_trough.ParabolicTrough.energy_func`
        """
        ######################################################################
        # equation for specified energy-group paremeters
        if self.energy_group.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.energy_func()

    def additional_derivatives(self, increment_filter, k):
        r"""Calculate partial derivatives for given additional equations."""
        ######################################################################
        # derivatives for specified energy-group paremeters
        if self.energy_group.is_set:
            f = self.energy_func
            self.jacobian[k, 0, 0] = (
                self.outl[0].h.val_SI - self.inl[0].h.val_SI)
            if not increment_filter[0, 1]:
                self.jacobian[k, 0, 1] = self.numeric_deriv(f, 'p', 0)
            if not increment_filter[0, 2]:
                self.jacobian[k, 0, 2] = self.numeric_deriv(f, 'h', 0)
            if not increment_filter[1, 1]:
                self.jacobian[k, 1, 1] = self.numeric_deriv(f, 'p', 1)
            if not increment_filter[1, 2]:
                self.jacobian[k, 1, 2] = self.numeric_deriv(f, 'h', 1)
            # custom variables for the energy-group
            for var in self.energy_group.elements:
                if var == self.Tamb:
                    continue
                if var.is_var:
                    self.jacobian[k, 2 + var.var_pos, 0] = (
                        self.numeric_deriv(f, self.vars[var], 2))
            k += 1

    def energy_func(self):
        r"""
        Equation for solar collector energy balance.

        Returns
        -------
        res : float
            Residual value of equation.

        Note
        ----
        .. math::

            \begin{split}
            T_m = & \frac{T_{out} + T_{in}}{2}\\
            iam = & 1 - iam_1 \cdot |aoi| - iam_2 \cdot aoi^2\\
            0 = & \dot{m} \cdot \left( h_{out} - h_{in} \right)\\
            & - A \cdot \left[E \cdot \eta_{opt} \cdot doc^{1.5} \cdot
            iam \right. \\
            & \left. - c_1 \cdot \left(T_m - T_{amb} \right) -
            c_2 \cdot \left(T_m - T_{amb}\right)^2
            \vphantom{ \eta_{opt} \cdot doc^{1.5}} \right]
            \end{split}

        Reference: :cite:`Janotte2014`.
        """
        i = self.inl[0].to_flow()
        o = self.outl[0].to_flow()

        T_m = (T_mix_ph(i, T0=self.inl[0].T.val_SI) +
               T_mix_ph(o, T0=self.outl[0].T.val_SI)) / 2

        iam = (
            1 - self.iam_1.val * abs(self.aoi.val) -
            self.iam_2.val * self.aoi.val ** 2)

        return (i[0] * (o[2] - i[2]) -
                self.A.val * (
                    self.E.val * self.eta_opt.val * self.doc.val ** 1.5 * iam -
                    (T_m - self.Tamb.val_SI) * self.c_1.val -
                    self.c_2.val * (T_m - self.Tamb.val_SI) ** 2))

    def calc_parameters(self):
        r"""Postprocessing parameter calculation."""
        i = self.inl[0].to_flow()
        o = self.outl[0].to_flow()

        self.Q.val = i[0] * (o[2] - i[2])
        self.pr.val = o[1] / i[1]
        self.zeta.val = ((i[1] - o[1]) * np.pi ** 2 / (
            4 * i[0] ** 2 * (self.inl[0].vol.val_SI + self.outl[0].vol.val_SI)
            ))
        if self.energy_group.is_set:
            self.Q_loss.val = self.E.val * self.A.val - self.Q.val

        self.check_parameter_bounds()
