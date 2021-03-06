# -*- coding: utf-8

"""Module of class HeatExchanger.


This file is part of project TESPy (github.com/oemof/tespy). It's copyrighted
by the contributors recorded in the version control history of the file,
available from its original location
tespy/components/heat_exchangers/heat_exchanger.py

SPDX-License-Identifier: MIT
"""

import warnings

import numpy as np

from tespy.components.component import Component
from tespy.tools.data_containers import ComponentCharacteristics as dc_cc
from tespy.tools.data_containers import ComponentProperties as dc_cp
from tespy.tools.data_containers import DataContainerSimple as dc_simple
from tespy.tools.fluid_properties import T_mix_ph
from tespy.tools.fluid_properties import h_mix_pT
from tespy.tools.fluid_properties import s_mix_ph
from tespy.tools.global_vars import err


class HeatExchanger(Component):
    r"""
    Class HeatExchanger is the parent class for Condenser and Desuperheater.

    The heat exchanger represents counter current heat exchangers. Both, hot
    and cold side of the heat exchanger, are simulated.

    Equations

        **mandatory equations**

        - :py:meth:`tespy.components.component.Component.fluid_func`
        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger.HeatExchanger.mass_flow_func`

        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger.HeatExchanger.energy_func`

        **optional equations**

        .. math::

            0 = \dot{m}_{in} \cdot \left(h_{out} - h_{in} \right) - \dot{Q}

        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger.HeatExchanger.kA_func`
        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger.HeatExchanger.kA_char_func`
        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger.HeatExchanger.ttd_u_func`
        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger.HeatExchanger.ttd_l_func`

        .. math::

            0 = p_{1,in} \cdot pr1 - p_{1,out}\\
            0 = p_{2,in} \cdot pr2 - p_{2,out}

        - hot side :py:meth:`tespy.components.component.Component.zeta_func`
        - cold side :py:meth:`tespy.components.component.Component.zeta_func`

        **additional equations**

        - :py:meth:`tespy.components.heat_exchangers.heat_exchanger.HeatExchanger.additional_equations`

    Inlets/Outlets

        - in1, in2 (index 1: hot side, index 2: cold side)
        - out1, out2 (index 1: hot side, index 2: cold side)

    Image

        .. image:: _images/HeatExchanger.svg
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

    pr1 : str, float, tespy.tools.data_containers.ComponentProperties
        Outlet to inlet pressure ratio at hot side, :math:`pr/1`.

    pr2 : str, float, tespy.tools.data_containers.ComponentProperties
        Outlet to inlet pressure ratio at cold side, :math:`pr/1`.

    zeta1 : str, float, tespy.tools.data_containers.ComponentProperties
        Geometry independent friction coefficient at hot side,
        :math:`\frac{\zeta}{D^4}/\frac{1}{\text{m}^4}`.

    zeta2 : str, float, tespy.tools.data_containers.ComponentProperties
        Geometry independent friction coefficient at cold side,
        :math:`\frac{\zeta}{D^4}/\frac{1}{\text{m}^4}`.

    kA : float, tespy.tools.data_containers.ComponentProperties
        Area independent heat transition coefficient,
        :math:`kA/\frac{\text{W}}{\text{K}}`.

    kA_char : tespy.tools.data_containers.DataContainerSimple
        Area independent heat transition coefficient characteristic.

    kA_char1 : tespy.tools.characteristics.CharLine, tespy.tools.data_containers.ComponentCharacteristics
        Characteristic line for hot side heat transfer coefficient.

    kA_char2 : tespy.tools.characteristics.CharLine, tespy.tools.data_containers.ComponentCharacteristics
        Characteristic line for cold side heat transfer coefficient.

    Note
    ----
    The HeatExchanger and subclasses (
    :py:class:`tespy.components.heat_exchangers.condenser.Condenser`,
    :py:class:`tespy.components.heat_exchangers.desuperheater.Desuperheater`)
    are countercurrent heat exchangers. Equations (:code:`kA`, :code:`ttd_u`,
    :code:`ttd_l`) do not work for directcurrent and crosscurrent or
    combinations of different types.

    Example
    -------
    A water cooling is installed to transfer heat from hot exhaust air. The
    heat exchanger is designed for a terminal temperature difference of 5 K.
    From this, it is possible to calculate the heat transfer coefficient and
    predict water and air outlet temperature in offdesign operation.

    >>> from tespy.components import Sink, Source, HeatExchanger
    >>> from tespy.connections import Connection
    >>> from tespy.networks import Network
    >>> import shutil
    >>> nw = Network(fluids=['water', 'air'], T_unit='C', p_unit='bar',
    ... h_unit='kJ / kg', iterinfo=False)
    >>> exhaust_hot = Source('Exhaust air outlet')
    >>> exhaust_cold = Sink('Exhaust air inlet')
    >>> cw_cold = Source('cooling water inlet')
    >>> cw_hot = Sink('cooling water outlet')
    >>> he = HeatExchanger('waste heat exchanger')
    >>> he.component()
    'heat exchanger'
    >>> ex_he = Connection(exhaust_hot, 'out1', he, 'in1')
    >>> he_ex = Connection(he, 'out1', exhaust_cold, 'in1')
    >>> cw_he = Connection(cw_cold, 'out1', he, 'in2')
    >>> he_cw = Connection(he, 'out2', cw_hot, 'in1')
    >>> nw.add_conns(ex_he, he_ex, cw_he, he_cw)

    The volumetric flow of the air is at 100 l/s. After designing the component
    it is possible to predict the temperature at different flow rates or
    different inlet temperatures of the exhaust air.

    >>> he.set_attr(pr1=0.98, pr2=0.98, ttd_u=5,
    ... design=['pr1', 'pr2', 'ttd_u'], offdesign=['zeta1', 'zeta2', 'kA_char'])
    >>> cw_he.set_attr(fluid={'air': 0, 'water': 1}, T=10, p=3,
    ... offdesign=['m'])
    >>> ex_he.set_attr(fluid={'air': 1, 'water': 0}, v=0.1, T=35)
    >>> he_ex.set_attr(T=17.5, p=1, design=['T'])
    >>> nw.solve('design')
    >>> nw.save('tmp')
    >>> round(ex_he.T.val - he_cw.T.val, 0)
    5.0
    >>> ex_he.set_attr(v=0.075)
    >>> nw.solve('offdesign', design_path='tmp')
    >>> round(he_cw.T.val, 1)
    27.5
    >>> round(he_ex.T.val, 1)
    14.4
    >>> ex_he.set_attr(v=0.1, T=40)
    >>> nw.solve('offdesign', design_path='tmp')
    >>> round(he_cw.T.val, 1)
    33.9
    >>> round(he_ex.T.val, 1)
    18.8
    >>> shutil.rmtree('./tmp', ignore_errors=True)
    """

    @staticmethod
    def component():
        return 'heat exchanger'

    @staticmethod
    def attr():
        return {
            'Q': dc_cp(max_val=0),
            'kA': dc_cp(min_val=0),
            'td_log': dc_cp(min_val=0),
            'ttd_u': dc_cp(min_val=0), 'ttd_l': dc_cp(min_val=0),
            'pr1': dc_cp(max_val=1), 'pr2': dc_cp(max_val=1),
            'zeta1': dc_cp(min_val=0), 'zeta2': dc_cp(min_val=0),
            'kA_char': dc_simple(),
            'kA_char1': dc_cc(param='m'), 'kA_char2': dc_cc(param='m')
        }

    @staticmethod
    def inlets():
        return ['in1', 'in2']

    @staticmethod
    def outlets():
        return ['out1', 'out2']

    def comp_init(self, nw):

        Component.comp_init(self, nw)

        # number of mandatroy equations for
        # fluid balance: num_fl * 2
        # mass flow: 2
        # energy balance: 1
        self.num_eq = self.num_nw_fluids * 2 + 3
        for var in [self.Q, self.kA, self.kA_char, self.ttd_u, self.ttd_l,
                    self.pr1, self.pr2, self.zeta1, self.zeta2]:
            if var.is_set:
                self.num_eq += 1

        if self.kA.is_set:
            msg = (
                'The usage of the parameter kA has changed for offdesign '
                'calculation. Specifying kA will keep a constant value for kA '
                'in the calculation. If you want to use the value adaption of '
                'kA by the characteristic line, please use kA_char as '
                'parameter instead (occurred at ' + self.label + '). This '
                'warning will disappear in TESPy version 0.4.0.')
            warnings.warn(msg, FutureWarning, stacklevel=2)

        self.jacobian = np.zeros((
            self.num_eq,
            self.num_i + self.num_o + self.num_vars,
            self.num_nw_vars))

        self.residual = np.zeros(self.num_eq)
        pos = self.num_nw_fluids * 2
        self.jacobian[0:pos] = self.fluid_deriv()
        self.jacobian[pos:pos + 2] = self.mass_flow_deriv()

    def equations(self):
        r"""Calculate residual vector with results of equations."""
        k = 0
        ######################################################################
        # equations for fluid balance
        self.residual[k:k + self.num_nw_fluids * 2] = self.fluid_func()
        k += self.num_nw_fluids * 2

        ######################################################################
        # equations for mass flow balance
        self.residual[k:k + 2] = self.mass_flow_func()
        k += 2

        ######################################################################
        # equations for energy balance
        self.residual[k] = self.energy_func()
        k += 1

        ######################################################################
        # equations for specified heat transfer
        if self.Q.is_set:
            self.residual[k] = (
                self.inl[0].m.val_SI * (
                    self.outl[0].h.val_SI - self.inl[0].h.val_SI) - self.Q.val)
            k += 1

        ######################################################################
        # equations for specified heat transfer coefficient
        if self.kA.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.kA_func()
            k += 1

        ######################################################################
        # equations for specified heat transfer coefficient characteristic
        if self.kA_char.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.kA_char_func()
            k += 1

        ######################################################################
        # equations for specified upper terminal temperature difference
        if self.ttd_u.is_set:
            self.residual[k] = self.ttd_u_func()
            k += 1

        ######################################################################
        # equations for specified lower terminal temperature difference
        if self.ttd_l.is_set:
            self.residual[k] = self.ttd_l_func()
            k += 1

        ######################################################################
        # equations for specified pressure ratio at hot side
        if self.pr1.is_set:
            self.residual[k] = (
                self.pr1.val * self.inl[0].p.val_SI - self.outl[0].p.val_SI)
            k += 1

        ######################################################################
        # equations for specified pressure ratio at cold side
        if self.pr2.is_set:
            self.residual[k] = (
                self.pr2.val * self.inl[1].p.val_SI - self.outl[1].p.val_SI)
            k += 1

        ######################################################################
        # equations for specified zeta at hot side
        if self.zeta1.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.zeta_func(
                    zeta='zeta1', inconn=0, outconn=0)
            k += 1

        ######################################################################
        # equations for specified zeta at cold side
        if self.zeta2.is_set:
            if (np.absolute(self.residual[k]) > err ** 2 or self.it % 4 == 0 or
                    self.always_all_equations):
                self.residual[k] = self.zeta_func(
                    zeta='zeta2', inconn=1, outconn=1)
            k += 1

        ######################################################################
        # additional equations
        self.additional_equations(k)

    def additional_equations(self, k):
        r"""Calculate results of additional equations."""
        return

    def derivatives(self, increment_filter):
        r"""
        Calculate partial derivatives for given equations.

        Returns
        -------
        mat_deriv : ndarray
            Matrix of partial derivatives.
        """
        ######################################################################
        # derivatives fluid and mass balance are static
        k = self.num_nw_fluids * 2 + 2

        ######################################################################
        # derivatives for energy balance equation
        for i in range(2):
            self.jacobian[k, i, 0] = (
                self.outl[i].h.val_SI - self.inl[i].h.val_SI)
            self.jacobian[k, i, 2] = -self.inl[i].m.val_SI

        self.jacobian[k, 2, 2] = self.inl[0].m.val_SI
        self.jacobian[k, 3, 2] = self.inl[1].m.val_SI
        k += 1

        ######################################################################
        # derivatives for specified heat transfer
        if self.Q.is_set:
            self.jacobian[k, 0, 0] = (
                self.outl[0].h.val_SI - self.inl[0].h.val_SI)
            self.jacobian[k, 0, 2] = -self.inl[0].m.val_SI
            self.jacobian[k, 2, 2] = self.inl[0].m.val_SI
            k += 1

        ######################################################################
        # derivatives for specified heat transfer coefficient
        if self.kA.is_set:
            f = self.kA_func
            self.jacobian[k, 0, 0] = (
                self.outl[0].h.val_SI - self.inl[0].h.val_SI)
            for i in range(4):
                if not increment_filter[i, 1]:
                    self.jacobian[k, i, 1] = self.numeric_deriv(f, 'p', i)
                if not increment_filter[i, 2]:
                    self.jacobian[k, i, 2] = self.numeric_deriv(f, 'h', i)
            k += 1

        ######################################################################
        # derivatives for specified heat transfer coefficient
        if self.kA_char.is_set:
            f = self.kA_char_func
            if not increment_filter[0, 0]:
                self.jacobian[k, 0, 0] = self.numeric_deriv(f, 'm', 0)
            if not increment_filter[1, 0]:
                self.jacobian[k, 1, 0] = self.numeric_deriv(f, 'm', 1)
            for i in range(4):
                if not increment_filter[i, 1]:
                    self.jacobian[k, i, 1] = self.numeric_deriv(f, 'p', i)
                if not increment_filter[i, 2]:
                    self.jacobian[k, i, 2] = self.numeric_deriv(f, 'h', i)
            k += 1

        ######################################################################
        # derivatives for specified upper terminal temperature difference
        if self.ttd_u.is_set:
            f = self.ttd_u_func
            for i in [0, 3]:
                if not increment_filter[i, 1]:
                    self.jacobian[k, i, 1] = self.numeric_deriv(f, 'p', i)
                if not increment_filter[i, 2]:
                    self.jacobian[k, i, 2] = self.numeric_deriv(f, 'h', i)
            k += 1

        ######################################################################
        # derivatives for specified lower terminal temperature difference
        if self.ttd_l.is_set:
            f = self.ttd_l_func
            for i in [1, 2]:
                if not increment_filter[i, 1]:
                    self.jacobian[k, i, 1] = self.numeric_deriv(f, 'p', i)
                if not increment_filter[i, 2]:
                    self.jacobian[k, i, 2] = self.numeric_deriv(f, 'h', i)
            k += 1

        ######################################################################
        # derivatives for specified pressure ratio at hot side
        if self.pr1.is_set:
            self.jacobian[k, 0, 1] = self.pr1.val
            self.jacobian[k, 2, 1] = -1
            k += 1

        ######################################################################
        # derivatives for specified pressure ratio at cold side
        if self.pr2.is_set:
            self.jacobian[k, 1, 1] = self.pr2.val
            self.jacobian[k, 3, 1] = -1
            k += 1

        ######################################################################
        # derivatives for specified zeta at hot side
        if self.zeta1.is_set:
            f = self.zeta_func
            if not increment_filter[0, 0]:
                self.jacobian[k, 0, 0] = self.numeric_deriv(
                    f, 'm', 0, zeta='zeta1', inconn=0, outconn=0)
            if not increment_filter[0, 1]:
                self.jacobian[k, 0, 1] = self.numeric_deriv(
                    f, 'p', 0, zeta='zeta1', inconn=0, outconn=0)
            if not increment_filter[0, 2]:
                self.jacobian[k, 0, 2] = self.numeric_deriv(
                    f, 'h', 0, zeta='zeta1', inconn=0, outconn=0)
            if not increment_filter[2, 1]:
                self.jacobian[k, 2, 1] = self.numeric_deriv(
                    f, 'p', 2, zeta='zeta1', inconn=0, outconn=0)
            if not increment_filter[2, 2]:
                self.jacobian[k, 2, 2] = self.numeric_deriv(
                    f, 'h', 2, zeta='zeta1', inconn=0, outconn=0)
            k += 1

        ######################################################################
        # derivatives for specified zeta at cold side
        if self.zeta2.is_set:
            f = self.zeta_func
            if not increment_filter[1, 0]:
                self.jacobian[k, 1, 0] = self.numeric_deriv(
                    f, 'm', 1, zeta='zeta2', inconn=1, outconn=1)
            if not increment_filter[1, 1]:
                self.jacobian[k, 1, 1] = self.numeric_deriv(
                    f, 'p', 1, zeta='zeta2', inconn=1, outconn=1)
            if not increment_filter[1, 2]:
                self.jacobian[k, 1, 2] = self.numeric_deriv(
                    f, 'h', 1, zeta='zeta2', inconn=1, outconn=1)
            if not increment_filter[3, 1]:
                self.jacobian[k, 3, 1] = self.numeric_deriv(
                    f, 'p', 3, zeta='zeta2', inconn=1, outconn=1)
            if not increment_filter[3, 2]:
                self.jacobian[k, 3, 2] = self.numeric_deriv(
                    f, 'h', 3, zeta='zeta2', inconn=1, outconn=1)
            k += 1

        ######################################################################
        # derivatives for additional equations
        self.additional_derivatives(increment_filter, k)

    def additional_derivatives(self, increment_filter, k):
        r"""Calculate partial derivatives for given additional equations."""
        return

    def mass_flow_func(self):
        r"""
        Calculate the residual value for mass flow balance equation.

        Returns
        -------
        residual : list
            Vector with residual value for component's mass flow balance.

            .. math::

                0 = \dot{m}_{in,i} - \dot{m}_{out,i} \;
                \forall i \in inlets/outlets
        """
        residual = []
        for i in range(self.num_i):
            residual += [self.inl[i].m.val_SI - self.outl[i].m.val_SI]
        return residual

    def mass_flow_deriv(self):
        r"""
        Calculate partial derivatives for all mass flow balance equations.

        Returns
        -------
        deriv : list
            Matrix with partial derivatives for the mass flow balance
            equations.
        """
        deriv = np.zeros((2, 4 + self.num_vars, self.num_nw_vars))
        for i in range(self.num_i):
            deriv[i, i, 0] = 1
        for j in range(self.num_o):
            deriv[j, j + i + 1, 0] = -1
        return deriv

    def energy_func(self):
        r"""
        Equation for heat exchanger energy balance.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                0 = \dot{m}_{1,in} \cdot \left(h_{1,out} - h_{1,in} \right) +
                \dot{m}_{2,in} \cdot \left(h_{2,out} - h_{2,in} \right)
        """
        return (
            self.inl[0].m.val_SI * (
                self.outl[0].h.val_SI - self.inl[0].h.val_SI) +
            self.inl[1].m.val_SI * (
                self.outl[1].h.val_SI - self.inl[1].h.val_SI))

    def kA_func(self):
        r"""
        Calculate heat transfer from heat transfer coefficient.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                res = \dot{m}_{1,in} \cdot \left( h_{1,out} - h_{1,in}\right) +
                kA \cdot \frac{T_{1,out} -
                T_{2,in} - T_{1,in} + T_{2,out}}
                {\ln{\frac{T_{1,out} - T_{2,in}}{T_{1,in} - T_{2,out}}}}

        Note
        ----
        For standard functions f\ :subscript:`1` \ and f\ :subscript:`2` \ see
        module :py:mod:`tespy.data`.

        - Calculate temperatures at inlets and outlets.
        - Perform value manipulation, if temperature levels are not physically
          feasible.
        """
        i1 = self.inl[0]
        i2 = self.inl[1]
        o1 = self.outl[0]
        o2 = self.outl[1]

        T_i1 = T_mix_ph(i1.to_flow(), T0=i1.T.val_SI)
        T_i2 = T_mix_ph(i2.to_flow(), T0=i2.T.val_SI)
        T_o1 = T_mix_ph(o1.to_flow(), T0=o1.T.val_SI)
        T_o2 = T_mix_ph(o2.to_flow(), T0=o2.T.val_SI)

        if T_i1 <= T_o2:
            T_i1 = T_o2 + 0.01
        if T_i1 <= T_o2:
            T_o2 = T_i1 - 0.01
        if T_i1 <= T_o2:
            T_o1 = T_i2 + 0.02
        if T_o1 <= T_i2:
            T_i2 = T_o1 - 0.02

        td_log = ((T_o1 - T_i2 - T_i1 + T_o2) /
                  np.log((T_o1 - T_i2) / (T_i1 - T_o2)))

        return i1.m.val_SI * (o1.h.val_SI - i1.h.val_SI) + self.kA.val * td_log

    def kA_char_func(self):
        r"""
        Calculate heat transfer from heat transfer coefficient characteristic.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                res = \dot{m}_{1,in} \cdot \left( h_{1,out} - h_{1,in}\right) +
                kA_{ref} \cdot f_{kA} \cdot \frac{T_{1,out} -
                T_{2,in} - T_{1,in} + T_{2,out}}
                {\ln{\frac{T_{1,out} - T_{2,in}}{T_{1,in} - T_{2,out}}}}

                f_{kA} = \frac{2}{
                \frac{1}{f_1\left(\frac{m_1}{m_{1,ref}}\right)} +
                \frac{1}{f_2\left(\frac{m_2}{m_{2,ref}}\right)}}

        Note
        ----
        For standard functions f\ :subscript:`1` \ and f\ :subscript:`2` \ see
        module :py:mod:`tespy.data`.

        - Calculate temperatures at inlets and outlets.
        - Perform value manipulation, if temperature levels are not physically
          feasible.
        """
        i1 = self.inl[0]
        i2 = self.inl[1]
        o1 = self.outl[0]
        o2 = self.outl[1]

        T_i1 = T_mix_ph(i1.to_flow(), T0=i1.T.val_SI)
        T_i2 = T_mix_ph(i2.to_flow(), T0=i2.T.val_SI)
        T_o1 = T_mix_ph(o1.to_flow(), T0=o1.T.val_SI)
        T_o2 = T_mix_ph(o2.to_flow(), T0=o2.T.val_SI)

        if T_i1 <= T_o2:
            T_i1 = T_o2 + 0.01
        if T_i1 <= T_o2:
            T_o2 = T_i1 - 0.01
        if T_i1 <= T_o2:
            T_o1 = T_i2 + 0.02
        if T_o1 <= T_i2:
            T_i2 = T_o1 - 0.02

        td_log = ((T_o1 - T_i2 - T_i1 + T_o2) /
                  np.log((T_o1 - T_i2) / (T_i1 - T_o2)))

        fkA1 = 1
        if self.kA_char1.param == 'm':
            fkA1 = self.kA_char1.func.evaluate(i1.m.val_SI / i1.m.design)

        fkA2 = 1
        if self.kA_char2.param == 'm':
            fkA2 = self.kA_char2.func.evaluate(i2.m.val_SI / i2.m.design)

        fkA = 2 / (1 / fkA1 + 1 / fkA2)

        return (
            i1.m.val_SI * (o1.h.val_SI - i1.h.val_SI) +
            self.kA.design * fkA * td_log)

    def ttd_u_func(self):
        r"""
        Equation for upper terminal temperature difference.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                res = ttd_{u} - T_{1,in} + T_{2,out}
        """
        T_i1 = T_mix_ph(self.inl[0].to_flow(), T0=self.inl[0].T.val_SI)
        T_o2 = T_mix_ph(self.outl[1].to_flow(), T0=self.outl[1].T.val_SI)
        return self.ttd_u.val - T_i1 + T_o2

    def ttd_l_func(self):
        r"""
        Equation for upper terminal temperature difference.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                res = ttd_{l} - T_{1,out} + T_{2,in}
        """
        i2 = self.inl[1].to_flow()
        o1 = self.outl[0].to_flow()
        return (self.ttd_l.val - T_mix_ph(o1, T0=self.outl[0].T.val_SI) +
                T_mix_ph(i2, T0=self.inl[1].T.val_SI))

    def bus_func(self, bus):
        r"""
        Calculate the value of the bus function.

        Parameters
        ----------
        bus : tespy.connections.bus.Bus
            TESPy bus object.

        Returns
        -------
        val : float
            Value of energy transfer :math:`\dot{E}`. This value is passed to
            :py:meth:`tespy.components.component.Component.calc_bus_value`
            for value manipulation according to the specified characteristic
            line of the bus.

            .. math::

                \dot{E} = \dot{m}_{1,in} \cdot \left(
                h_{1,out} - h_{1,in} \right)
        """
        i = self.inl[0].to_flow()
        o = self.outl[0].to_flow()
        val = i[0] * (o[2] - i[2])

        return val

    def bus_deriv(self, bus):
        r"""
        Calculate partial derivatives of the bus function.

        Parameters
        ----------
        bus : tespy.connections.bus.Bus
            TESPy bus object.

        Returns
        -------
        mat_deriv : ndarray
            Matrix of partial derivatives.
        """
        deriv = np.zeros((1, 4, self.num_nw_vars))
        f = self.calc_bus_value
        deriv[0, 0, 0] = self.numeric_deriv(f, 'm', 0, bus=bus)
        deriv[0, 0, 2] = self.numeric_deriv(f, 'h', 0, bus=bus)
        deriv[0, 2, 2] = self.numeric_deriv(f, 'h', 2, bus=bus)
        return deriv

    def initialise_source(self, c, key):
        r"""
        Return a starting value for pressure and enthalpy at outlet.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                4 \cdot 10^5 & \text{key = 'p'}\\
                h\left(p, 200 \text{K} \right) & \text{key = 'h' at outlet 1}\\
                h\left(p, 250 \text{K} \right) & \text{key = 'h' at outlet 2}
                \end{cases}
        """
        if key == 'p':
            return 50e5
        elif key == 'h':
            flow = c.to_flow()
            if c.source_id == 'out1':
                T = 200 + 273.15
                return h_mix_pT(flow, T)
            else:
                T = 250 + 273.15
                return h_mix_pT(flow, T)

    def initialise_target(self, c, key):
        r"""
        Return a starting value for pressure and enthalpy at inlet.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                4 \cdot 10^5 & \text{key = 'p'}\\
                h\left(p, 300 \text{K} \right) & \text{key = 'h' at inlet 1}\\
                h\left(p, 220 \text{K} \right) & \text{key = 'h' at outlet 2}
                \end{cases}
        """
        if key == 'p':
            return 50e5
        elif key == 'h':
            flow = c.to_flow()
            if c.target_id == 'in1':
                T = 300 + 273.15
                return h_mix_pT(flow, T)
            else:
                T = 220 + 273.15
                return h_mix_pT(flow, T)

    def calc_parameters(self):
        r"""Postprocessing parameter calculation."""
        # component parameters
        self.Q.val = self.inl[0].m.val_SI * (
            self.outl[0].h.val_SI - self.inl[0].h.val_SI)
        self.ttd_u.val = self.inl[0].T.val_SI - self.outl[1].T.val_SI
        self.ttd_l.val = self.outl[0].T.val_SI - self.inl[1].T.val_SI

        # pr and zeta
        for i in range(2):
            self.get_attr('pr' + str(i + 1)).val = (
                self.outl[i].p.val_SI / self.inl[i].p.val_SI)
            self.get_attr('zeta' + str(i + 1)).val = (
                (self.inl[i].p.val_SI - self.outl[i].p.val_SI) * np.pi ** 2 / (
                    4 * self.inl[i].m.val_SI ** 2 *
                    (self.inl[i].vol.val_SI + self.outl[i].vol.val_SI)
                ))

        # kA and logarithmic temperature difference
        if self.ttd_u.val < 0 or self.ttd_l.val < 0:
            self.td_log.val = np.nan
            self.kA.val = np.nan
        else:
            self.td_log.val = ((self.ttd_l.val - self.ttd_u.val) /
                               np.log(self.ttd_l.val / self.ttd_u.val))
            self.kA.val = -self.Q.val / self.td_log.val

        if self.kA_char.is_set:
            # get bound errors for kA hot side characteristics
            if self.kA_char1.param == 'm':
                if not np.isnan(self.inl[0].m.design):
                    self.kA_char1.func.get_bound_errors(
                        self.inl[0].m.val_SI / self.inl[0].m.design,
                        self.label)

            # get bound errors for kA copld side characteristics
            if self.kA_char2.param == 'm':
                if not np.isnan(self.inl[1].m.design):
                    self.kA_char2.func.get_bound_errors(
                        self.inl[1].m.val_SI / self.inl[1].m.design,
                        self.label)

        self.check_parameter_bounds()

    def entropy_balance(self):
        r"""
        Calculate entropy balance of a heat exchanger.

        The allocation of the entropy streams due to heat exchanged and due to
        irreversibility is performed by solving for T on both sides of the heat
        exchanger:

        .. math::

            h_\mathrm{out} - h_\mathrm{in} = \int_\mathrm{in}^\mathrm{out} v
            \cdot dp - \int_\mathrm{in}^\mathrm{out} T \cdot ds

        As solving :math:`\int_\mathrm{in}^\mathrm{out} v \cdot dp` for non
        isobaric processes would require perfect process knowledge (the path)
        on how specific volume and pressure change throught the component, the
        heat transfer is splitted into three separate virtual processes for
        both sides:

        - in->in*: decrease pressure to
          :math:`p_\mathrm{in*}=p_\mathrm{in}\cdot\sqrt{\frac{p_\mathrm{out}}{p_\mathrm{in}}}`
          without changing enthalpy.
        - in*->out* transfer heat without changing pressure.
          :math:`h_\mathrm{out*}-h_\mathrm{in*}=h_\mathrm{out}-h_\mathrm{in}`
        - out*->out decrease pressure to outlet pressure :math:`p_\mathrm{out}`
          without changing enthalpy.

        Note
        ----
        The entropy balance makes the follwing parameter available:

        .. math::

            \text{S\_Q1}=\dot{m} \cdot \left(s_\mathrm{out*,1}-s_\mathrm{in*,1}
            \right)\\
            \text{S\_Q2}=\dot{m} \cdot \left(s_\mathrm{out*,2}-s_\mathrm{in*,2}
            \right)\\
            \text{S\_Qirr}=\text{S\_Q2} - \text{S\_Q1}\\
            \text{S\_irr1}=\dot{m} \cdot \left(s_\mathrm{out,1}-s_\mathrm{in,1}
            \right) - \text{S\_Q1}\\
            \text{S\_irr2}=\dot{m} \cdot \left(s_\mathrm{out,2}-s_\mathrm{in,2}
            \right) - \text{S\_Q2}\\
            \text{S\_irr}=\sum \dot{S}_\mathrm{irr}\\
            \text{T\_mQ1}=\frac{\dot{Q}}{\text{S\_Q1}}\\
            \text{T\_mQ2}=\frac{\dot{Q}}{\text{S\_Q2}}
        """
        self.S_irr = 0
        for i in range(2):
            inl = self.inl[i]
            out = self.outl[i]
            p_star = inl.p.val_SI * (
                self.get_attr('pr' + str(i + 1)).val) ** 0.5
            s_i_star = s_mix_ph(
                [0, p_star, inl.h.val_SI, inl.fluid.val], T0=inl.T.val_SI)
            s_o_star = s_mix_ph(
                [0, p_star, out.h.val_SI, out.fluid.val], T0=out.T.val_SI)

            setattr(self, 'S_Q' + str(i + 1),
                    inl.m.val_SI * (s_o_star - s_i_star))
            S_Q = self.get_attr('S_Q' + str(i + 1))
            setattr(self, 'S_irr' + str(i + 1),
                    inl.m.val_SI * (out.s.val_SI - inl.s.val_SI) - S_Q)
            setattr(self, 'T_mQ' + str(i + 1),
                    inl.m.val_SI * (out.h.val_SI - inl.h.val_SI) / S_Q)

            self.S_irr += self.get_attr('S_irr' + str(i + 1))

        self.S_irr += self.S_Q1 + self.S_Q2

    def exergy_balance(self, T0):
        r"""
        Calculate exergy balance of a heat exchanger.

        Parameters
        ----------
        T0 : float
            Ambient temperature T0 / K.

        Note
        ----
        .. math::

            \dot{E}_\mathrm{P} =
            \begin{cases}
            \dot{E}_\mathrm{out,2}^\mathrm{T} -
            \dot{E}_\mathrm{in,2}^\mathrm{T}
            & T_\mathrm{in,1}, T_\mathrm{in,2}, T_\mathrm{out,1},
            T_\mathrm{out,2} > T_0\\
            \dot{E}_\mathrm{out,1}^\mathrm{T} -
            \dot{E}_\mathrm{in,1}^\mathrm{T}
            & T_0 \geq  T_\mathrm{in,1}, T_\mathrm{in,2}, T_\mathrm{out,1},
            T_\mathrm{out,2}\\
            \dot{E}_\mathrm{out,1}^\mathrm{T} +
            \dot{E}_\mathrm{out,2}^\mathrm{T}
            & T_\mathrm{in,1}, T_\mathrm{out,2} > T_0 \geq
            T_\mathrm{in,2}, T_\mathrm{out,1}\\
            \dot{E}_\mathrm{out,1}^\mathrm{T}
            & T_\mathrm{in,1} > T_0 \geq
            T_\mathrm{in,2}, T_\mathrm{out,1}, T_\mathrm{out,2}\\
            0
            & T_\mathrm{in,1}, T_\mathrm{out,1} > T_0 \geq
            T_\mathrm{in,2}, T_\mathrm{out,2}\\
            \dot{E}_\mathrm{out,2}^\mathrm{T}
            & T_\mathrm{in,1}, T_\mathrm{out,1},
            T_\mathrm{out,2} \geq T_0 > T_\mathrm{in,2}\\
            \end{cases}

            \dot{E}_\mathrm{F} =
            \begin{cases}
            \dot{E}_\mathrm{in,1}^\mathrm{PH} -
            \dot{E}_\mathrm{out,1}^\mathrm{PH} +
            \dot{E}_\mathrm{in,2}^\mathrm{M} -
            \dot{E}_\mathrm{out,2}^\mathrm{M}
            & T_\mathrm{in,1}, T_\mathrm{in,2}, T_\mathrm{out,1},
            T_\mathrm{out,2} > T_0\\
            \dot{E}_\mathrm{in,2}^\mathrm{PH} -
            \dot{E}_\mathrm{out,2}^\mathrm{PH} +
            \dot{E}_\mathrm{in,1}^\mathrm{M} -
            \dot{E}_\mathrm{out,1}^\mathrm{M}
            & T_0 \geq T_\mathrm{in,1}, T_\mathrm{in,2}, T_\mathrm{out,1},
            T_\mathrm{out,2}\\
            \dot{E}_\mathrm{in,1}^\mathrm{PH} +
            \dot{E}_\mathrm{in,2}^\mathrm{PH} -
            \dot{E}_\mathrm{out,1}^\mathrm{M} -
            \dot{E}_\mathrm{out,2}^\mathrm{M}
            & T_\mathrm{in,1}, T_\mathrm{out,2} > T_0 \geq
            T_\mathrm{in,2}, T_\mathrm{out,1}\\
            \dot{E}_\mathrm{in,1}^\mathrm{PH} +
            \dot{E}_\mathrm{in,2}^\mathrm{PH} -
            \dot{E}_\mathrm{out,2}^\mathrm{PH} -
            \dot{E}_\mathrm{out,1}^\mathrm{M}
            & T_\mathrm{in,1} > T_0 \geq
            T_\mathrm{in,2}, T_\mathrm{out,1}, T_\mathrm{out,2}\\
            \dot{E}_\mathrm{in,1}^\mathrm{PH} -
            \dot{E}_\mathrm{out,1}^\mathrm{PH} +
            \dot{E}_\mathrm{in,2}^\mathrm{PH} -
            \dot{E}_\mathrm{out,2}^\mathrm{PH}
            & T_\mathrm{in,1}, T_\mathrm{out,1} > T_0 \geq
            T_\mathrm{in,2}, T_\mathrm{out,2}\\
            \dot{E}_\mathrm{in,1}^\mathrm{PH} -
            \dot{E}_\mathrm{out,1}^\mathrm{PH} +
            \dot{E}_\mathrm{in,2}^\mathrm{PH} -
            \dot{E}_\mathrm{out,2}^\mathrm{M}
            & T_\mathrm{in,1}, T_\mathrm{out,1},
            T_\mathrm{out,2} \geq T_0 > T_\mathrm{in,2}\\
            \end{cases}
        """
        if all([c.T.val_SI > T0 for c in self.inl + self.outl]):
            self.E_P = self.outl[1].Ex_therm - self.inl[1].Ex_therm
            self.E_F = self.inl[0].Ex_physical - self.outl[0].Ex_physical + (
                self.inl[1].Ex_mech - self.outl[1].Ex_mech)
        elif all([c.T.val_SI <= T0 for c in self.inl + self.outl]):
            self.E_P = self.outl[0].Ex_therm - self.inl[0].Ex_therm
            self.E_F = self.inl[1].Ex_physical - self.outl[1].Ex_physical + (
                self.inl[0].Ex_mech - self.outl[0].Ex_mech)
        elif (self.inl[0].T.val_SI > T0 and self.outl[1].T.val_SI > T0 and
              self.outl[0].T.val_SI <= T0 and self.inl[1].T.val_SI <= T0):
            self.E_P = self.outl[0].Ex_therm + self.outl[1].Ex_therm
            self.E_F = self.inl[0].Ex_physical + self.inl[1].Ex_physical - (
                self.outl[0].Ex_mech + self.outl[1].Ex_mech)
        elif (self.inl[0].T.val_SI > T0 and self.inl[1].T.val_SI <= T0 and
              self.outl[0].T.val_SI <= T0 and self.outl[1].T.val_SI <= T0):
            self.E_P = self.outl[0].Ex_therm
            self.E_F = self.inl[0].Ex_physical + self.inl[1].Ex_physical - (
                self.outl[1].Ex_physical + self.outl[0].Ex_mech)
        elif (self.inl[0].T.val_SI > T0 and self.outl[0].T.val_SI > T0 and
              self.inl[1].T.val_SI <= T0 and self.outl[1].T.val_SI <= T0):
            self.E_P = 0
            self.E_F = self.inl[0].Ex_physical - self.outl[0].Ex_physical + (
                self.inl[1].Ex_physical - self.outl[1].Ex_physical)
        else:
            self.E_P = self.outl[1].Ex_therm
            self.E_F = self.inl[0].Ex_physical - self.outl[0].Ex_physical + (
                self.inl[1].Ex_physical - self.outl[1].Ex_mech)

        self.E_bus = np.nan
        if np.isnan(self.E_P):
            self.E_D = self.E_F
        else:
            self.E_D = self.E_F - self.E_P
        self.epsilon = self.E_P / self.E_F

    def get_plotting_data(self):
        """Generate a dictionary containing FluProDia plotting information.

        Returns
        -------
        data : dict
            A nested dictionary containing the keywords required by the
            :code:`calc_individual_isoline` method of the
            :code:`FluidPropertyDiagram` class. First level keys are the
            connection index ('in1' -> 'out1', therefore :code:`1` etc.).
        """
        return {
            i + 1: {
                'isoline_property': 'p',
                'isoline_value': self.inl[i].p.val,
                'isoline_value_end': self.outl[i].p.val,
                'starting_point_property': 'v',
                'starting_point_value': self.inl[i].vol.val,
                'ending_point_property': 'v',
                'ending_point_value': self.outl[i].vol.val
            } for i in range(2)}
