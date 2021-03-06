# -*- coding: utf-8

"""Module for tespy network class.

The network is the container for every TESPy simulation. The network class
automatically creates the system of equations describing topology and
parametrisation of a specific model and solves it.


This file is part of project TESPy (github.com/oemof/tespy). It's copyrighted
by the contributors recorded in the version control history of the file,
available from its original location tespy/networks/networks.py

SPDX-License-Identifier: MIT
"""
import ast
import json
import logging
import os
from collections import Counter
from collections import OrderedDict
from time import time

import numpy as np
import pandas as pd
from numpy.linalg import norm
from tabulate import tabulate

from tespy import connections as con
from tespy.tools import fluid_properties as fp
from tespy.tools import helpers as hlp
from tespy.tools.data_containers import ComponentCharacteristicMaps as dc_cm
from tespy.tools.data_containers import ComponentCharacteristics as dc_cc
from tespy.tools.data_containers import ComponentProperties as dc_cp
from tespy.tools.data_containers import DataContainerSimple as dc_simple
from tespy.tools.data_containers import GroupedComponentProperties as dc_gcp
from tespy.tools.global_vars import coloring
from tespy.tools.global_vars import err

# Only require cupy if Cuda shall be used
try:
    import cupy as cu
except ModuleNotFoundError:
    cu = None


class Network:
    r"""
    Class component is the base class of all TESPy components.

    Parameters
    ----------
    fluids : list
        A list of all fluids within the network container.

    memorise_fluid_properties : boolean
        Activate or deactivate fluid property value memorisation. Default
        state is activated (:code:`True`).

    h_range : list
        List with minimum and maximum values for enthalpy value range.

    h_unit : str
        Specify the unit for enthalpy: 'J / kg', 'kJ / kg', 'MJ / kg'.

    iterinfo : boolean
        Print convergence progress to console.

    m_range : list
        List with minimum and maximum values for mass flow value range.

    m_unit : str
        Specify the unit for mass flow: 'kg / s', 't / h'.

    p_range : list
        List with minimum and maximum values for pressure value range.

    p_unit : str
        Specify the unit for pressure: 'Pa', 'psi', 'bar', 'MPa'.

    s_unit : str
        Specify the unit for specific entropy: 'J / kgK', 'kJ / kgK',
        'MJ / kgK'.

    T_unit : str
        Specify the unit for temperature: 'K', 'C', 'F', 'R'.

    v_unit : str
        Specify the unit for volumetric flow: 'm3 / s', 'm3 / h', 'l / s',
        'l / h'.

    vol_unit : str
        Specify the unit for specific volume: 'm3 / kg', 'l / kg'.

    Note
    ----
    Unit specification is optional: If not specified the SI unit (first
    element in above lists) will be applied!

    Range specification is optional, too. The value range is used to stabilise
    the newton algorith. For more information see the "getting started" section
    in the online-documentation.

    Example
    -------
    Basic example for a setting up a tespy.networks.network.Network object. Specifying
    the fluids is mandatory! Unit systems, fluid property range and iterinfo
    are optional.

    Standard value for iterinfo is :code:`True`. This will print out
    convergence progress to the console. You can suop the printouts by setting
    this property to :code:`False`.

    >>> from tespy.networks import Network
    >>> fluid_list = ['water', 'air', 'R134a']
    >>> mynetwork = Network(fluids=fluid_list, p_unit='bar', T_unit='C')
    >>> mynetwork.set_attr(p_range=[1, 10])
    >>> type(mynetwork)
    <class 'tespy.networks.network.Network'>
    >>> mynetwork.set_attr(iterinfo=False)
    >>> mynetwork.iterinfo
    False
    >>> mynetwork.set_attr(iterinfo=True)
    >>> mynetwork.iterinfo
    True

    A simple network consisting of a source, a pipe and a sink. This example
    shows how the printout parameter can be used. We specify
    :code:`printout=False` for both connections, the pipe as well as the heat
    bus. Therefore the :code:`.print_results()` method should not print any
    results.

    >>> from tespy.networks import Network
    >>> from tespy.components import Source, Sink, Pipe
    >>> from tespy.connections import Connection, Bus
    >>> nw = Network(['CH4'], T_unit='C', p_unit='bar', v_unit='m3 / s')
    >>> so = Source('source')
    >>> si = Sink('sink')
    >>> p = Pipe('pipe', Q=0, pr=0.95, printout=False)
    >>> a = Connection(so, 'out1', p, 'in1')
    >>> b = Connection(p, 'out1', si, 'in1')
    >>> nw.add_conns(a, b)
    >>> a.set_attr(fluid={'CH4': 1}, T=30, p=10, m=10, printout=False)
    >>> b.set_attr(printout=False)
    >>> b = Bus('heat bus')
    >>> b.add_comps({'c': p})
    >>> nw.add_busses(b)
    >>> b.set_attr(printout=False)
    >>> nw.set_attr(iterinfo=False)
    >>> nw.solve('design')
    >>> nw.print_results()
    """

    def __init__(self, fluids, memorise_fluid_properties=True, **kwargs):

        # fluid list and constants
        if isinstance(fluids, list):
            self.fluids = sorted(fluids)
        else:
            msg = ('Please provide a list containing the network\'s fluids on '
                   'creation.')
            logging.error(msg)
            raise TypeError(msg)

        self.set_defaults()
        self.set_fluid_back_ends(memorise_fluid_properties)
        self.set_attr(**kwargs)

    def set_defaults(self):
        """Set default network properties."""
        # connection dataframe
        self.conns = pd.DataFrame(
            columns=['source', 'source_id', 'target', 'target_id'])
        # connection dictionary for fast access
        self.connections = {}
        # component dictionary for fast access
        self.components = {}
        # bus dictionary
        self.busses = OrderedDict()

        # in case of a design calculation after an offdesign calculation
        self.redesign = False

        self.checked = False
        self.design_path = None
        self.iterinfo = True

        # written propteries
        self.props = {
            'm': 'mass flow', 'v': 'volumetric flow', 'p': 'pressure',
            'h': 'enthalpy', 'T': 'temperature', 'vol': 'specific volume',
            'x': 'vapour mass fraction', 's': 'entropy'
        }

        # available unit systems
        # mass flow
        self.m = {'kg / s': 1, 't / h': 3.6}
        # pressure
        self.p = {'Pa': 1, 'psi': 6.8948e3, 'bar': 1e5, 'MPa': 1e6}
        # specific enthalpy
        self.h = {'J / kg': 1, 'kJ / kg': 1e3, 'MJ / kg': 1e6}
        # specific volume
        self.vol = {'m3 / kg': 1, 'l / kg': 1e-3}
        # specific entropy
        self.s = {'J / kgK': 1, 'kJ / kgK': 1e3, 'MJ / kgK': 1e6}
        # temperature
        self.T = {
            'C': [273.15, 1], 'F': [459.67, 5 / 9], 'K': [0, 1],
            'R': [0, 5 / 9]
        }
        # volumetric flow
        self.v = {
            'm3 / s': 1, 'l / s': 1e-3, 'm3 / h': 1 / 3600, 'l / h': 1 / 3.6
        }
        # vapor mass fraction
        self.x = {'-': 1, '%': 1e-2}

        # SI unit specification
        self.SI_units = {
            'm': 'kg / s', 'v': 'm3 / s', 'p': 'Pa', 'h': 'J / kg', 'T': 'K',
            'vol': 'm3 / kg', 'x': '-', 's': 'J / kgK'
        }

        for prop in self.props.keys():
            # standard unit set
            self.__dict__.update({prop + '_unit': self.SI_units[prop]})

        msg = (
            'Default unit specifications: mass flow: ' + self.m_unit + ', ' +
            'pressure: ' + self.p_unit + ', ' + 'enthalpy: ' + self.h_unit +
            ', ' + 'temperature: ' + self.T_unit + ', ' + 'specific volume: ' +
            self.vol_unit + ', ' + 'entropy: ' + self.s_unit + ', ' +
            'vapour mass fraction: ' + self.x_unit + ', ' +
            'volumetric flow: ' + self.v_unit + '.')
        logging.debug(msg)

        # generic value range
        self.m_range_SI = np.array([-1e12, 1e12])
        self.p_range_SI = np.array([2e2, 300e5])
        self.h_range_SI = np.array([1e3, 7e6])

        for prop in ['m', 'p', 'h']:
            limits = self.get_attr(prop + '_range_SI')
            msg = (
                'Default ' + self.props[prop] + ' limits, min: ' +
                str(limits[0]) + ' ' + self.SI_units[prop] + ', max: ' +
                str(limits[1]) + ' ' + self.SI_units[prop] + '.')
            logging.debug(msg)

    def set_fluid_back_ends(self, memorise_fluid_properties):
        """Set the fluid back ends."""
        # this must be ordered as the fluid property memorisation calls
        # the mass fractions of the different fluids as keys in a given order.
        self.fluids_backends = OrderedDict()

        msg = 'Network fluids are: '
        i = 0
        for f in self.fluids:
            try:
                data = f.split('::')
                backend = data[0]
                fluid = data[1]
            except IndexError:
                backend = 'HEOS'
                fluid = f

            self.fluids_backends[fluid] = backend
            self.fluids[i] = fluid

            msg += fluid + ', '
            i += 1

        msg = msg[:-2] + '.'
        logging.debug(msg)

        # initialise fluid property memorisation function for this network
        fp.Memorise.add_fluids(self.fluids_backends, memorise_fluid_properties)

    def set_attr(self, **kwargs):
        r"""
        Set, resets or unsets attributes of a network.

        Parameters
        ----------
        h_range : list
            List with minimum and maximum values for enthalpy value range.

        h_unit : str
            Specify the unit for enthalpy: 'J / kg', 'kJ / kg', 'MJ / kg'.

        iterinfo : boolean
            Print convergence progress to console.

        m_range : list
            List with minimum and maximum values for mass flow value range.

        m_unit : str
            Specify the unit for mass flow: 'kg / s', 't / h'.

        p_range : list
            List with minimum and maximum values for pressure value range.

        p_unit : str
            Specify the unit for pressure: 'Pa', 'psi', 'bar', 'MPa'.

        s_unit : str
            Specify the unit for specific entropy: 'J / kgK', 'kJ / kgK',
            'MJ / kgK'.

        T_unit : str
            Specify the unit for temperature: 'K', 'C', 'F', 'R'.

        v_unit : str
            Specify the unit for volumetric flow: 'm3 / s', 'm3 / h', 'l / s',
            'l / h'.

        vol_unit : str
            Specify the unit for specific volume: 'm3 / kg', 'l / kg'.
        """
        # unit sets
        for prop in self.SI_units.keys():
            unit = prop + '_unit'
            if unit in kwargs.keys():
                if kwargs[unit] not in self.get_attr(prop).keys():
                    msg = ('Allowed units for ' + self.props[prop] + ' are: ' +
                           str(self.get_attr(prop).keys()))
                    logging.error(msg)
                    raise ValueError(msg)
                else:
                    self.__dict__.update({unit: kwargs[unit]})
                    msg = ('Setting ' + self.props[prop] + ' unit: ' +
                           kwargs[unit] + '.')
                    logging.debug(msg)

        for prop in ['m', 'p', 'h']:
            if prop + '_range' in kwargs.keys():
                if not isinstance(kwargs[prop + '_range'], list):
                    msg = (
                        'Specify the value range as list: [' + prop +
                        '_min, ' + prop + '_max]')
                    logging.error(msg)
                    raise TypeError(msg)
                else:
                    self.__dict__.update(
                        {prop + '_range_SI':
                         np.array(kwargs[prop + '_range']) *
                         self.get_attr(prop)[self.get_attr(prop + '_unit')]})

                limits = self.get_attr(prop + '_range_SI')
                msg = (
                    'Setting ' + self.props[prop] + ' limits, min: ' +
                    str(limits[0]) + ' ' + self.SI_units[prop] + ', max: ' +
                    str(limits[1]) + ' ' + self.SI_units[prop] + '.')
                logging.debug(msg)

        # update non SI value ranges
        for prop in ['m', 'p', 'h']:
            self.__dict__.update({
                prop + '_range': self.convert_from_SI(
                    prop, self.get_attr(prop + '_range_SI'),
                    self.get_attr(prop + '_unit')
                )
            })

        self.iterinfo = kwargs.get('iterinfo', self.iterinfo)

        if not isinstance(self.iterinfo, bool):
            msg = ('Network parameter iterinfo must be True or False!')
            logging.error(msg)
            raise TypeError(msg)

    def convert_to_SI(self, property, value, unit):
        r"""
        Convert a value to its SI value.

        Parameters
        ----------
        property : str
            Fluid property to convert.

        value : float
            Value to convert.

        unit : str
            Unit of the value.

        Returns
        -------
        SI_value : float
            Specified fluid property in SI value.
        """
        if property == 'T':
            return (value + self.T[unit][0]) * self.T[unit][1]

        elif property == 'Td_bp':
            return value * self.T[unit][1]

        else:
            return value * self.get_attr(property)[unit]

    def convert_from_SI(self, property, SI_value, unit):
        r"""
        Get a value in the network's unit system from SI value.

        Parameters
        ----------
        property : str
            Fluid property to convert.

        SI_value : float
            SI value to convert.

        unit : str
            Unit of the value.

        Returns
        -------
        value : float
            Specified fluid property value in network's unit system.
        """
        if property == 'T':
            return SI_value / self.T[unit][1] - self.T[unit][0]

        elif property == 'Td_bp':
            return SI_value / self.T[unit][1]

        else:
            return SI_value / self.get_attr(property)[unit]

    def get_attr(self, key):
        r"""
        Get the value of a network attribute.

        Parameters
        ----------
        key : str
            The attribute you want to retrieve.

        Returns
        -------
        out :
            Specified attribute.
        """
        if key in self.__dict__:
            return self.__dict__[key]
        else:
            msg = 'Network has no attribute \"' + str(key) + '\".'
            logging.error(msg)
            raise KeyError(msg)

    def add_subsys(self, *args):
        r"""
        Add one or more subsystems to the network.

        Parameters
        ----------
        c : tespy.components.subsystem.Subsystem
            The subsystem to be added to the network, subsystem objects si
            :code:`network.add_subsys(s1, s2, s3, ...)`.
        """
        for subsys in args:
            for c in subsys.conns.values():
                self.add_conns(c)

    def add_conns(self, *args):
        r"""
        Add one or more connections to the network.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            The connection to be added to the network, connections objects ci
            :code:`add_conns(c1, c2, c3, ...)`.
        """
        for c in args:
            if not isinstance(c, con.Connection):
                msg = ('Must provide tespy.connections.connection.Connection '
                       'objects as parameters.')
                logging.error(msg)
                raise TypeError(msg)

            elif c.label in self.connections.keys():
                msg = (
                    'There is already a connection with the label ' +
                    c.label + '. The connection labels must be unique!')
                logging.error(msg)
                raise ValueError(msg)

            c.good_starting_values = False

            self.conns.loc[c] = [c.source, c.source_id, c.target, c.target_id]
            # for fast access
            self.connections[c.label] = c

            msg = 'Added connection ' + c.label + ' to network.'
            logging.debug(msg)
            # set status "checked" to false, if conneciton is added to network.
            self.checked = False

    def del_conns(self, *args):
        """
        Remove one or more connections from the network.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            The connection to be removed from the network, connections objects
            ci :code:`del_conns(c1, c2, c3, ...)`.
        """
        for c in args:
            self.conns = self.conns.drop(c)
            del self.connections[c.label]
            msg = ('Deleted connection ' + c.label + ' from network.')
            logging.debug(msg)
        # set status "checked" to false, if conneciton is deleted from network.
        self.checked = False

    def check_conns(self):
        r"""Check connections for multiple usage of inlets or outlets."""
        dub = self.conns.loc[
            self.conns.duplicated(['source', 'source_id']) == True]  # noqa: E712
        for c in dub.index:
            targets = ''
            for conns in self.conns[
                    (self.conns['source'] == c.source) &
                    (self.conns['source_id'] == c.source_id)].index:
                targets += conns.target.label + ' (' + conns.target_id + '); '

            msg = (
                'The source ' + c.source.label + ' (' + c.source_id +
                ') is attached '
                'to more than one target: ' + targets[:-2] + '. '
                'Please check your network.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

        dub = self.conns.loc[
            self.conns.duplicated(['target', 'target_id']) == True]  # noqa: E712
        for c in dub.index:
            sources = ''
            for conns in self.conns[
                    (self.conns['target'] == c.target) &
                    (self.conns['target_id'] == c.target_id)].index:
                sources += conns.source.label + ' (' + conns.source_id + '); '

            msg = (
                'The target ' + c.target.label + ' (' + c.target_id +
                ') is attached to more than one source: ' + sources[:-2] + '. '
                'Please check your network.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

    def add_busses(self, *args):
        r"""
        Add one or more busses to the network.

        Parameters
        ----------
        b : tespy.connections.bus.Bus
            The bus to be added to the network, bus objects bi
            :code:`add_busses(b1, b2, b3, ...)`.
        """
        for b in args:
            if self.check_busses(b):
                self.busses[b.label] = b
                msg = 'Added bus ' + b.label + ' to network.'
                logging.debug(msg)

    def del_busses(self, *args):
        r"""
        Remove one or more busses from the network.

        Parameters
        ----------
        b : tespy.connections.bus.Bus
            The bus to be removed from the network, bus objects bi
            :code:`add_busses(b1, b2, b3, ...)`.
        """
        for b in args:
            if b in self.busses.values():
                del self.busses[b.label]
                msg = 'Deleted bus ' + b.label + ' from network.'
                logging.debug(msg)

    def check_busses(self, b):
        r"""
        Checksthe busses to be added for type, duplicates and identical labels.

        Parameters
        ----------
        b : tespy.connections.bus.Bus
            The bus to be checked.
        """
        if isinstance(b, con.Bus):
            if len(self.busses) > 0:
                if b in self.busses.values():
                    msg = ('Network contains the bus ' + b.label + ' (' +
                           str(b) + ') already.')
                    logging.error(msg)
                    raise hlp.TESPyNetworkError(msg)
                elif b.label in self.busses.keys():
                    msg = ('Network already has a bus with the name ' +
                           b.label + '.')
                    logging.error(msg)
                    raise hlp.TESPyNetworkError(msg)
                else:
                    return True
            else:
                return True
        else:
            msg = 'Only objects of type bus are allowed in *args.'
            logging.error(msg)
            raise TypeError(msg)

    def check_network(self):
        r"""Check if components are connected properly within the network."""
        self.check_conns()
        # get unique components in connections dataframe
        comps = pd.unique(self.conns[['source', 'target']].values.ravel())
        # build the dataframe for components
        self.init_components(comps)
        # count number of incoming and outgoing connections and compare to
        # expected values
        for comp in self.comps.index:
            num_o = (self.conns[['source', 'target']] == comp).sum().source
            num_i = (self.conns[['source', 'target']] == comp).sum().target
            if num_o != comp.num_o:
                msg = (
                    comp.label + ' is missing ' + str(comp.num_o - num_o) + ' '
                    'outgoing connections. Make sure all outlets are connected'
                    ' and all connections have been added to the network.')
                logging.error(msg)
                # raise an error in case network check is unsuccesful
                raise hlp.TESPyNetworkError(msg)
            elif num_i != comp.num_i:
                msg = (
                    comp.label + ' is missing ' + str(comp.num_i - num_i) + ' '
                    'incoming connections. Make sure all inlets are connected '
                    'and all connections have been added to the network.')
                logging.error(msg)
                # raise an error in case network check is unsuccesful
                raise hlp.TESPyNetworkError(msg)

        # network checked
        self.checked = True
        msg = 'Networkcheck successful.'
        logging.info(msg)

    def init_components(self, comps):
        r"""
        Set up a dataframe for the network's components.

        Additionally, check, if all components have unique labels.

        Parameters
        ----------
        comps : pandas.core.frame.DataFrame
            DataFrame containing all components of the network gathered from
            the network's connection information.

        Note
        ----
        The dataframe for the components is derived from the network's
        connections. Thus it does not hold any additional information, the
        dataframe is used to simplify the code, only.
        """
        self.comps = pd.DataFrame(index=comps)

        labels = []
        for comp in comps:
            # this is required for printing and saving
            self.comps.loc[comp, 'comp_type'] = comp.__class__.__name__
            self.comps.loc[comp, 'label'] = comp.label
            # get for incoming and outgoing connections of a component
            sources = self.conns[self.conns['source'] == comp]
            sources = sources['source_id'].sort_values().index.tolist()
            targets = self.conns[self.conns['target'] == comp]
            targets = targets['target_id'].sort_values().index.tolist()
            # save the incoming and outgoing as well as the number of
            # connections as component attribute
            comp.inl = targets
            comp.outl = sources
            comp.num_i = len(comp.inlets())
            comp.num_o = len(comp.outlets())
            labels += [comp.label]
            # for fast access
            self.components[comp.label] = comp

            # save the connection locations to the components
            comp.conn_loc = []
            for c in comp.inl + comp.outl:
                comp.conn_loc += [self.conns.index.get_loc(c)]

        # check for duplicates in the component labels
        if len(labels) != len(list(set(labels))):
            duplicates = [
                item for item, count in Counter(labels).items() if count > 1]
            msg = ('All Components must have unique labels, duplicates are: ' +
                   str(duplicates) + '.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

    def initialise(self):
        r"""
        Initilialise the network depending on calclation mode.

        Design

        - Generic fluid composition and fluid property initialisation.
        - Starting values from initialisation path if provided.

        Offdesign

        - Check offdesign path specification.
        - Set component and connection design point properties.
        - Switch from design/offdesign parameter specification.
        """
        if len(self.conns) == 0:
            msg = (
                'No connections have been added to the network, please make '
                'sure to add your connections with the .add_conns() method.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

        if len(self.fluids) == 0:
            msg = ('Network has no fluids, please specify a list with fluids '
                   'on network creation.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

        # keep track of the number of bus, component and connection equations
        # as well as number of component variables
        self.num_bus_eq = 0
        self.num_comp_eq = 0
        self.num_conn_eq = 0
        self.num_comp_vars = 0
        self.init_set_properties()

        if self.mode == 'offdesign':
            self.redesign = True
            if self.design_path is None:
                # must provide design_path
                msg = ('Please provide "design_path" for every offdesign '
                       'calculation.')
                logging.error(msg)
                raise hlp.TESPyNetworkError(msg)

            # load design case
            if self.new_design:
                self.init_offdesign_params()

            self.init_offdesign()

        else:
            # reset any preceding offdesign calculation
            self.init_design()
            # generic fluid initialisation
            # for offdesign cases good starting values should be available
            self.init_fluids()

        # generic fluid property initialisation
        self.init_properties()

        msg = 'Network initialised.'
        logging.info(msg)

    def init_set_properties(self):
        """Specification of SI values for user set values."""
        # fluid property values
        for c in self.conns.index:
            # reindex connections dictionary
            self.connections[c.label] = c
            if not self.init_previous:
                c.good_starting_values = False

            c.conn_loc = self.conns.index.get_loc(c)

            for key in ['m', 'p', 'h', 'T', 'x', 'v', 'Td_bp', 'vol', 's']:
                # read unit specifications
                if not c.get_attr(key).unit_set:
                    if key == 'Td_bp':
                        c.get_attr(key).unit = self.get_attr('T_unit')
                    else:
                        c.get_attr(key).unit = self.get_attr(key + '_unit')
                # set SI value
                if c.get_attr(key).val_set:
                    c.get_attr(key).val_SI = self.convert_to_SI(
                        key, c.get_attr(key).val, c.get_attr(key).unit)

            # fluid vector specification
            tmp = c.fluid.val
            for fluid in tmp.keys():
                if fluid not in self.fluids:
                    msg = ('Your connection ' + c.label + ' holds a fluid, '
                           'that is not part of the networks\'s fluids (' +
                           fluid + ').')
                    raise hlp.TESPyNetworkError(msg)
            tmp0 = c.fluid.val0
            tmp_set = c.fluid.val_set
            c.fluid.val = OrderedDict()
            c.fluid.val0 = OrderedDict()
            c.fluid.val_set = OrderedDict()

            # if the number of fluids is one the mass fraction is 1 for every
            # connection
            if len(self.fluids) == 1:
                c.fluid.val[self.fluids[0]] = 1
                c.fluid.val0[self.fluids[0]] = 1
                if self.fluids[0] in tmp_set.keys():
                    c.fluid.val_set[self.fluids[0]] = tmp_set[self.fluids[0]]
                else:
                    c.fluid.val_set[self.fluids[0]] = False

                # jump to next connection
                continue

            for fluid in self.fluids:
                # take over values from temporary dicts
                if fluid in tmp.keys() and fluid in tmp_set.keys():
                    c.fluid.val[fluid] = tmp[fluid]
                    c.fluid.val0[fluid] = tmp[fluid]
                    c.fluid.val_set[fluid] = tmp_set[fluid]
                # take over starting values
                elif fluid in tmp0.keys():
                    if fluid not in tmp_set.keys():
                        c.fluid.val[fluid] = tmp0[fluid]
                        c.fluid.val0[fluid] = tmp0[fluid]
                        c.fluid.val_set[fluid] = False
                # if fluid not in keys
                else:
                    c.fluid.val[fluid] = 0
                    c.fluid.val0[fluid] = 0
                    c.fluid.val_set[fluid] = False

        msg = (
            'Updated fluid property SI values and fluid mass fraction for '
            'user specified connection parameters.')
        logging.debug(msg)

    def init_design(self):
        r"""
        Initialise a design calculation.

        Offdesign parameters are unset, design parameters are set. If
        :code:`local_offdesign` is :code:`True` for connections or components,
        the design point information are read from the .csv-files in the
        respective :code:`design_path`. In this case, the design values are
        unset, the offdesign values set.
        """
        # connections
        for c in self.conns.index:
            # read design point information of connections with
            # local_offdesign activated from their respective design path
            if c.local_offdesign:
                if c.design_path is None:
                    msg = (
                        'The parameter local_offdesign is True for the '
                        'connection ' + c.label + ', an individual '
                        'design_path must be specified in this case!')
                    logging.error(msg)
                    raise hlp.TESPyNetworkError(msg)

                # unset design parameters
                for var in c.design:
                    c.get_attr(var).val_set = False
                # set offdesign parameters
                for var in c.offdesign:
                    c.get_attr(var).val_set = True

                # read design point information
                df = self.init_read_connections(c.design_path)
                msg = (
                    'Reading individual design point information for '
                    'connection ' + c.label + ' from path ' + c.design_path +
                    'connections.')
                logging.debug(msg)

                # write data to connections
                self.init_conn_design_params(c, df)

            else:
                # unset all design values
                c.m.design = np.nan
                c.p.design = np.nan
                c.h.design = np.nan
                c.fluid.design = OrderedDict()

                c.new_design = True

                # switch connections to design mode
                if self.redesign:
                    for var in c.design:
                        c.get_attr(var).val_set = True

                    for var in c.offdesign:
                        c.get_attr(var).val_set = False

        # unset design values for busses, count bus equations and
        # reindex bus dicitonary
        for b in self.busses.values():
            self.busses[b.label] = b
            self.num_bus_eq += b.P.is_set * 1
            for cp in b.comps.index:
                b.comps.loc[cp, 'P_ref'] = np.nan

        series = pd.Series(dtype=np.float64)
        for cp in self.comps.index:
            # reindex components dicitonary
            self.components[cp.label] = cp
            # read design point information of components with
            # local_offdesign activated from their respective design path
            if cp.local_offdesign:
                if cp.design_path is not None:
                    # get type of component (class name)
                    c = cp.__class__.__name__
                    # read design point information
                    path = hlp.modify_path_os(
                        cp.design_path + '/components/' + c + '.csv')
                    df = pd.read_csv(
                        path, sep=';', decimal='.', converters={
                            'busses': ast.literal_eval,
                            'bus_P_ref': ast.literal_eval})
                    df.set_index('label', inplace=True)
                    # write data
                    self.init_comp_design_params(cp, df.loc[cp.label])

                # unset design parameters
                for var in cp.design:
                    cp.get_attr(var).is_set = False

                # set offdesign parameters
                switched = False
                msg = 'Set component attributes '

                for var in cp.offdesign:
                    # set variables provided in .offdesign attribute
                    data = cp.get_attr(var)
                    data.is_set = True

                    # take nominal values from design point
                    if isinstance(data, dc_cp):
                        cp.get_attr(var).val = cp.get_attr(var).design
                        switched = True
                        msg += var + ', '

                if switched:
                    msg = (msg[:-2] + ' to design value at component ' +
                           cp.label + '.')
                    logging.debug(msg)

                cp.new_design = False

            else:
                # switch connections to design mode
                if self.redesign:
                    for var in cp.design:
                        cp.get_attr(var).is_set = True

                    for var in cp.offdesign:
                        cp.get_attr(var).is_set = False

                cp.set_parameters(self.mode, series)

            # component initialisation
            cp.comp_init(self)
            # count number of component equations and variables
            self.num_comp_vars += cp.num_vars
            self.num_comp_eq += cp.num_eq

    def init_offdesign_params(self):
        r"""
        Read design point information from specified :code:`design_path`.

        If a :code:`design_path` has been specified individually for components
        or connections, the data will be read from the specified individual
        path instead.

        Note
        ----
        The methods
        :py:meth:`tespy.networks.network.Network.init_comp_design_params`
        (components) and the
        :py:meth:`tespy.networks.network.Network.init_conn_design_params`
        (connections) handle the parameter specification.
        """
        # components without any parameters
        not_required = [
            'source', 'sink', 'node', 'merge', 'splitter', 'separator', 'drum',
            'subsystem_interface', 'droplet_separator']
        # fetch all components, reindex with label
        df_comps = self.comps.copy()
        df_comps['comp_obj'] = df_comps.index
        df_comps.set_index('label', inplace=True)
        df_comps = df_comps[~df_comps['comp_type'].isin(not_required)]

        # iter through unique types of components (class names)
        for c in df_comps['comp_type'].unique():
            path = hlp.modify_path_os(
                self.design_path + '/components/' + c + '.csv')
            msg = (
                'Reading design point information for components of type '
                + c + ' from path ' + path + '.')
            logging.debug(msg)

            # read data
            df = pd.read_csv(
                path, sep=';', decimal='.', converters={
                    'busses': ast.literal_eval,
                    'bus_P_ref': ast.literal_eval})
            df.set_index('label', inplace=True)
            # iter through all components of this type and set data
            for c_label in df.index:
                comp = df_comps.loc[c_label, 'comp_obj']
                # read data of components with individual design_path
                if comp.design_path is not None:
                    path_c = hlp.modify_path_os(
                        comp.design_path + '/components/' + c + '.csv')
                    df_c = pd.read_csv(
                        path_c, sep=';', decimal='.', converters={
                             'busses': ast.literal_eval,
                             'bus_P_ref': ast.literal_eval})
                    df_c.set_index('label', inplace=True)
                    data = df_c.loc[comp.label]

                else:
                    data = df.loc[comp.label]

                # write data to components
                self.init_comp_design_params(comp, data)

        msg = 'Done reading design point information for components.'
        logging.debug(msg)

        # read connection design point information
        df = self.init_read_connections(self.design_path)
        msg = (
            'Reading design point information for connections from path ' +
            self.design_path + '/connections.csv.')
        logging.debug(msg)

        # iter through connections
        for c in self.conns.index:

            # read data of connections with individual design_path
            if c.design_path is not None:
                df_c = self.init_read_connections(c.design_path)
                msg = (
                    'Reading individual design point information for '
                    'connection ' + c.label + ' from path ' + c.design_path +
                    '/connections.csv.')
                logging.debug(msg)

                # write data
                self.init_conn_design_params(c, df_c)

            else:
                # write data
                self.init_conn_design_params(c, df)

        msg = 'Done reading design point information for connections.'
        logging.debug(msg)

    def init_comp_design_params(self, component, data):
        r"""
        Write design point information to components.

        Parameters
        ----------
        component : tespy.components.component.Component
            Write design point information to this component.

        data : pandas.core.series.Series, pandas.core.frame.DataFrame
            Design point information.
        """
        # write component design data
        component.set_parameters(self.mode, data)
        # write design values to busses
        i = 0
        for b in data.busses:
            bus = self.busses[b].comps
            bus.loc[component, 'P_ref'] = data['bus_P_ref'][i]
            i += 1

    def init_conn_design_params(self, c, df):
        r"""
        Write design point information to connections.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Write design point information to this connection.

        df : pandas.core.frame.DataFrame
            Dataframe containing design point information.
        """
        # match connection (source, source_id, target, target_id) on
        # connection objects of design file
        conn = df.loc[
            df['source'].isin([c.source.label]) &
            df['target'].isin([c.target.label]) &
            df['source_id'].isin([c.source_id]) &
            df['target_id'].isin([c.target_id])]

        try:
            # read connection information
            conn_id = conn.index[0]
            for var in ['m', 'p', 'h', 'v', 'x', 'T', 'Td_bp']:
                c.get_attr(var).design = self.convert_to_SI(
                    var, df.loc[conn_id, var], df.loc[conn_id, var + '_unit'])
            c.vol.design = c.v.design / c.m.design
            for fluid in self.fluids:
                c.fluid.design[fluid] = df.loc[conn_id, fluid]
        except IndexError:
            # no matches in the connections of the network and the design files
            msg = (
                'Could not find connection ' + c.label + ' in design case. '
                'Please, make sure no connections have been modified or '
                'components have been relabeled for your offdesign '
                'calculation.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

    def init_offdesign(self):
        r"""
        Switch components and connections from design to offdesign mode.

        Note
        ----
        **components**

        All parameters stated in the component's attribute :code:`cp.design`
        will be unset and all parameters stated in the component's attribute
        :code:`cp.offdesign` will be set instead.

        Additionally, all component parameters specified as variables are
        unset and the values from design point are set.

        **connections**

        All parameters given in the connection's attribute :code:`c.design`
        will be unset and all parameters stated in the connections's attribute
        :code:`cp.offdesign` will be set instead. This does also affect
        referenced values!
        """
        for c in self.conns.index:
            if not c.local_design:
                # switch connections to offdesign mode
                for var in c.design:
                    c.get_attr(var).val_set = False
                    c.get_attr(var).ref_set = False

                for var in c.offdesign:
                    c.get_attr(var).val_set = True
                    c.get_attr(var).val_SI = c.get_attr(var).design

                c.new_design = False

        msg = 'Switched connections from design to offdesign.'
        logging.debug(msg)

        for cp in self.comps.index:
            # reindex components dicitonary
            self.components[cp.label] = cp
            if not cp.local_design:
                # unset variables provided in .design attribute
                for var in cp.design:
                    cp.get_attr(var).is_set = False

                switched = False
                msg = 'Set component attributes '

                for var in cp.offdesign:
                    # set variables provided in .offdesign attribute
                    data = cp.get_attr(var)
                    data.is_set = True

                    # take nominal values from design point
                    if isinstance(data, dc_cp):
                        cp.get_attr(var).val = cp.get_attr(var).design
                        switched = True
                        msg += var + ', '

                if switched:
                    msg = (msg[:-2] + ' to design value at component ' +
                           cp.label + '.')
                    logging.debug(msg)

            # start component initialisation
            cp.comp_init(self)
            cp.new_design = False
            self.num_comp_vars += cp.num_vars
            self.num_comp_eq += cp.num_eq

        msg = 'Switched components from design to offdesign.'
        logging.debug(msg)

        # count bus equations and reindex bus dicitonary
        for b in self.busses.values():
            self.busses[b.label] = b
            self.num_bus_eq += b.P.is_set * 1

    def init_fluids(self):
        r"""
        Initialise the fluid vector on every connection of the network.

        - Create fluid vector for every component as dict,
          index: nw.fluids,
          values: 0 if not set by user.
        - Create fluid_set vector with same logic,
          index: nw.fluids,
          values: False if not set by user.
        - If there are any combustion chambers in the network, calculate fluid
          vector starting from there.
        - Propagate fluid vector in direction of sources and targets.
        """
        # stop fluid propagation for single fluid networks
        if len(self.fluids) == 1:
            return

        # fluid propagation from set values
        for c in self.conns.index:
            if any(c.fluid.val_set.values()):
                c.target.propagate_fluid_to_target(c, c.target)
                c.source.propagate_fluid_to_source(c, c.source)

        # fluid starting value generation for components
        for cp in self.comps.index:
            cp.initialise_fluids()

        msg = 'Fluid initialisation done.'
        logging.debug(msg)

    def init_properties(self):
        """
        Initialise the fluid properties on every connection of the network.

        - Set generic starting values for mass flow, enthalpy and pressure if
          not user specified, read from :code:`ìnit_path` or available from
          previous calculation.
        - For generic starting values precalculate enthalpy value at points of
          given temperature, vapor mass fraction, temperature difference to
          boiling point or fluid state.
        """
        if self.init_path is not None:
            df = self.init_read_connections(self.init_path)
        # improved starting values for referenced connections,
        # specified vapour content values, temperature values as well as
        # subccooling/overheating and state specification
        for c in self.conns.index:
            if self.init_path is not None:
                conn = df.loc[
                    df['source'].isin([c.source.label]) &
                    df['target'].isin([c.target.label]) &
                    df['source_id'].isin([c.source_id]) &
                    df['target_id'].isin([c.target_id])]
                try:
                    conn_id = conn.index[0]
                    # overwrite SI-values with values from init_file,
                    # except user specified values
                    for prop in ['m', 'p', 'h']:
                        data = c.get_attr(prop)
                        data.val0 = df.loc[conn_id, prop]
                        data.unit = df.loc[conn_id, prop + '_unit']

                    for fluid in self.fluids:
                        if not c.fluid.val_set[fluid]:
                            c.fluid.val[fluid] = df.loc[conn_id, fluid]
                        c.fluid.val0[fluid] = c.fluid.val[fluid]

                    c.good_starting_values = True

                except IndexError:
                    msg = (
                        'Could not find connection ' + c.label + ' in '
                        'connections.csv of init_path ' + self.init_path + '.')
                    logging.debug(msg)

            for key in ['m', 'p', 'h']:
                if not c.good_starting_values:
                    self.init_val0(c, key)
                if not c.get_attr(key).val_set:
                    c.get_attr(key).val_SI = self.convert_to_SI(
                        key, c.get_attr(key).val0, c.get_attr(key).unit)

            self.init_count_connections_parameters(c)

        for c in self.conns.index:
            if not c.good_starting_values:
                for key in ['m', 'p', 'h', 'T']:
                    if (c.get_attr(key).ref_set and
                            not c.get_attr(key).val_set):
                        c.get_attr(key).val_SI = (
                                c.get_attr(key).ref.obj.get_attr(key).val_SI *
                                c.get_attr(key).ref.f + c.get_attr(key).ref.d)

                self.init_precalc_properties(c)

            # starting values for specified subcooling/overheating
            # and state specification. These should be recalculated even with
            # good starting values, for example, when one exchanges enthalpy
            # with boiling point temperature difference.
            if ((c.Td_bp.val_set or c.state.is_set) and
                    not c.h.val_set):
                if ((c.Td_bp.val_SI > 0 and c.Td_bp.val_set) or
                        (c.state.val == 'g' and c.state.is_set)):
                    h = fp.h_mix_pQ(c.to_flow(), 1)
                    if c.h.val_SI < h:
                        c.h.val_SI = h * 1.001
                elif ((c.Td_bp.val_SI < 0 and c.Td_bp.val_set) or
                      (c.state.val == 'l' and c.state.is_set)):
                    h = fp.h_mix_pQ(c.to_flow(), 0)
                    if c.h.val_SI > h:
                        c.h.val_SI = h * 0.999

        msg = 'Generic fluid property specification complete.'
        logging.debug(msg)

    def init_count_connections_parameters(self, c):
        """
        Count the number of parameters set on a connection.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection count parameters of.
        """
        self.num_conn_eq += [
            c.m.val_set, c.p.val_set, c.h.val_set, c.T.val_set,
            c.x.val_set, c.v.val_set, c.Td_bp.val_set].count(True)
        self.num_conn_eq += [
            c.m.ref_set, c.p.ref_set, c.h.ref_set, c.T.ref_set].count(True)
        self.num_conn_eq += list(c.fluid.val_set.values()).count(True)
        self.num_conn_eq += c.fluid.balance * 1

    def init_precalc_properties(self, c):
        """
        Precalculate enthalpy values for connections.

        Precalculation is performed only if temperature, vapor mass fraction,
        temperature difference to boiling point or phase is specified.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to precalculate values for.
        """
        # starting values for specified vapour content or temperature
        if c.x.val_set and not c.h.val_set:
            try:
                c.h.val_SI = fp.h_mix_pQ(c.to_flow(), c.x.val_SI)
            except ValueError:
                pass

        if c.T.val_set and not c.h.val_set:
            try:
                c.h.val_SI = fp.h_mix_pT(c.to_flow(), c.T.val_SI)
            except ValueError:
                pass

    def init_val0(self, c, key):
        r"""
        Set starting values for fluid properties.

        The component classes provide generic starting values for their inlets
        and outlets.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to initialise.
        """
        if np.isnan(c.get_attr(key).val0):
            # starting value for mass flow is 1 kg/s
            if key == 'm':
                c.get_attr(key).val0 = 1

            # generic starting values for pressure and enthalpy
            else:
                # retrieve starting values from component information
                val_s = c.source.initialise_source(c, key)
                val_t = c.target.initialise_target(c, key)

                if val_s == 0 and val_t == 0:
                    if key == 'p':
                        c.get_attr(key).val0 = 1e5
                    elif key == 'h':
                        c.get_attr(key).val0 = 1e6

                elif val_s == 0:
                    c.get_attr(key).val0 = val_t
                elif val_t == 0:
                    c.get_attr(key).val0 = val_s
                else:
                    c.get_attr(key).val0 = (val_s + val_t) / 2

                # change value according to specified unit system
                c.get_attr(key).val0 = self.convert_from_SI(
                    key, c.get_attr(key).val0, self.get_attr(key + '_unit'))

    @staticmethod
    def init_read_connections(base_path):
        r"""
        Read connection information from base_path.

        Parameters
        ----------
        base_path : str
            Path to network information.
        """
        path = hlp.modify_path_os(base_path + '/connections.csv')
        df = pd.read_csv(path, index_col=0, delimiter=';', decimal='.')
        return df

    def solve(self, mode, init_path=None, design_path=None,
              max_iter=50, min_iter=4, init_only=False, init_previous=True,
              use_cuda=False, always_all_equations=True):
        r"""
        Solve the network.

        - Check network consistency.
        - Initialise calculation and preprocessing.
        - Perform actual calculation.
        - Postprocessing.

        Parameters
        ----------
        mode : str
            Choose from 'design' and 'offdesign'.

        init_path : str
            Path to the folder, where your network was saved to, e.g.
            saving to :code:`nw.save('myplant/tests')` would require loading
            from :code:`init_path='myplant/tests'`.

        design_path : str
            Path to the folder, where your network's design case was saved to,
            e.g. saving to :code:`nw.save('myplant/tests')` would require
            loading from :code:`design_path='myplant/tests'`.

        max_iter : int
            Maximum number of iterations before calculation stops, default: 50.

        min_iter : int
            Minimum number of iterations before calculation stops, default: 4.

        init_only : boolean
            Perform initialisation only, default: :code:`False`.

        init_previous : boolean
            Initialise the calculation with values from the previous
            calculation, default: :code:`True`.

        use_cuda : boolean
            Use cuda instead of numpy for matrix inversion, default:
            :code:`False`.

        always_all_equations : boolean
            Calculate all equations in every iteration. Disabling this flag,
            will increase calculation speed, especially for mixtures, default:
            :code:`True`.

        Note
        ----
        For more information on the solution process have a look at the online
        documentation at tespy.readthedocs.io in the section "TESPy modules".
        """
        self.new_design = False
        if self.design_path == design_path and design_path is not None:
            for c in self.conns.index:
                if c.new_design:
                    self.new_design = True
                    break
            if not self.new_design:
                for cp in self.comps.index:
                    if cp.new_design:
                        self.new_design = True
                        break

        else:
            self.new_design = True

        self.init_path = init_path
        self.design_path = design_path
        self.max_iter = max_iter
        self.min_iter = min_iter
        self.init_previous = init_previous
        self.iter = 0
        self.use_cuda = use_cuda
        self.always_all_equations = always_all_equations

        if self.use_cuda and cu is None:
            msg = ('Specifying use_cuda=True requires cupy to be installed on '
                   'your machine. Numpy will be used instead.')
            logging.warning(msg)
            self.use_cuda = False

        if mode != 'offdesign' and mode != 'design':
            msg = 'Mode must be "design" or "offdesign".'
            logging.error(msg)
            raise ValueError(msg)
        else:
            self.mode = mode

        msg = (
            'Solver properties: mode=' + self.mode + ', init_path=' +
            str(self.init_path) + ', design_path=' + str(self.design_path) +
            ', max_iter=' + str(max_iter) + ', min_iter=' + str(min_iter) +
            ', init_only=' + str(init_only))
        logging.debug(msg)

        if not self.checked:
            self.check_network()

        msg = (
            'Network properties: '
            'number of components=' + str(len(self.comps)) +
            ', number of connections=' + str(len(self.conns.index)) +
            ', number of busses=' + str(len(self.busses)))
        logging.debug(msg)

        self.initialise()

        if init_only:
            return

        msg = 'Starting solver.'
        logging.info(msg)

        self.solve_determination()
        self.solve_loop()

        if self.lin_dep:
            msg = (
                'Singularity in jacobian matrix, calculation aborted! Make '
                'sure your network does not have any linear dependencies in '
                'the parametrisation. Other reasons might be\n-> given '
                'temperature with given pressure in two phase region, try '
                'setting enthalpy instead or provide accurate starting value '
                'for pressure.\n-> given logarithmic temperature differences '
                'or kA-values for heat exchangers, \n-> support better '
                'starting values.\n-> bad starting value for fuel mass flow '
                'of combustion chamber, provide small (near to zero, but not '
                'zero) starting value.')
            logging.error(msg)
            return

        self.postprocessing()
        fp.Memorise.del_memory(self.fluids)

        if not self.progress:
            msg = (
                'The solver does not seem to make any progress, aborting '
                'calculation. Residual value is '
                '{:.2e}'.format(norm(self.residual)) + '. This frequently '
                'happens, if the solver pushes the fluid properties out of '
                'their feasible range.')
            logging.warning(msg)
            return

        msg = 'Calculation complete.'
        logging.info(msg)

    def solve_loop(self):
        r"""Loop of the newton algorithm."""
        # parameter definitions
        self.res = np.array([])
        self.residual = np.zeros([self.num_vars])
        self.increment = np.ones([self.num_vars])
        self.jacobian = np.zeros((self.num_vars, self.num_vars))

        self.start_time = time()
        self.progress = True

        if self.iterinfo:
            self.print_iterinfo_head()

        for self.iter in range(self.max_iter):

            self.increment_filter = np.absolute(self.increment) < err ** 2
            self.solve_control()
            self.res = np.append(self.res, norm(self.residual))

            if self.iterinfo:
                self.print_iterinfo_body()

            if ((self.iter >= self.min_iter and self.res[-1] < err ** 0.5) or
                    self.lin_dep):
                break

            if self.iter > 40:
                if (all(self.res[(self.iter - 3):] >= self.res[-3] * 0.95) and
                        self.res[-1] >= self.res[-2] * 0.95):
                    self.progress = False
                    break

        self.end_time = time()

        self.print_iterinfo_tail()

        if self.iter == self.max_iter - 1:
            msg = ('Reached maximum iteration count (' + str(self.max_iter) +
                   '), calculation stopped. Residual value is '
                   '{:.2e}'.format(norm(self.residual)))
            logging.warning(msg)

    def solve_determination(self):
        r"""Check, if the number of supplied parameters is sufficient."""
        # number of variables per connection
        self.num_conn_vars = len(self.fluids) + 3

        # total number of variables
        self.num_vars = (
            self.num_conn_vars * len(self.conns.index) + self.num_comp_vars)

        msg = 'Number of connection equations: ' + str(self.num_conn_eq) + '.'
        logging.debug(msg)

        msg = 'Number of bus equations: ' + str(self.num_bus_eq) + '.'
        logging.debug(msg)

        msg = 'Number of component equations: ' + str(self.num_comp_eq) + '.'
        logging.debug(msg)

        msg = 'Total number of variables: ' + str(self.num_vars) + '.'
        logging.debug(msg)
        msg = 'Number of component variables: ' + str(self.num_comp_vars) + '.'
        logging.debug(msg)
        msg = ('Number of connection variables: ' +
               str(self.num_conn_vars * len(self.conns.index)) + '.')
        logging.debug(msg)

        n = self.num_comp_eq + self.num_conn_eq + self.num_bus_eq
        if n > self.num_vars:
            msg = ('You have provided too many parameters: ' +
                   str(self.num_vars) + ' required, ' + str(n) +
                   ' supplied. Aborting calculation!')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)
        elif n < self.num_vars:
            msg = ('You have not provided enough parameters: '
                   + str(self.num_vars) + ' required, ' + str(n) +
                   ' supplied. Aborting calculation!')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

    def print_iterinfo_head(self):
        """Print head of convergence progress."""
        if self.num_comp_vars == 0:
            # iterinfo printout without any custom variables
            msg = (
                'iter\t| residual | massflow | pressure | enthalpy | fluid\n')
            msg += '-' * 8 + '+----------' * 4 + '+' + '-' * 9

        else:
            # iterinfo printout with custom variables in network
            msg = ('iter\t| residual | massflow | pressure | enthalpy | '
                   'fluid    | custom\n')
            msg += '-' * 8 + '+----------' * 5 + '+' + '-' * 9

        print(msg)

    def print_iterinfo_body(self):
        """Print convergence progress."""
        vec = self.increment[0:-(self.num_comp_vars + 1)]
        msg = (str(self.iter + 1))

        if not self.lin_dep and not np.isnan(norm(self.residual)):
            msg += '\t| ' + '{:.2e}'.format(norm(self.residual))
            msg += ' | ' + '{:.2e}'.format(norm(vec[0::self.num_conn_vars]))
            msg += ' | ' + '{:.2e}'.format(norm(vec[1::self.num_conn_vars]))
            msg += ' | ' + '{:.2e}'.format(norm(vec[2::self.num_conn_vars]))

            ls = []
            for f in range(len(self.fluids)):
                ls += vec[3 + f::self.num_conn_vars].tolist()

            msg += ' | ' + '{:.2e}'.format(norm(ls))

            if self.num_comp_vars > 0:
                msg += ' | ' + '{:.2e}'.format(norm(
                    self.increment[-self.num_comp_vars:]))

        else:
            if np.isnan(norm(self.residual)):
                msg += '\t|      nan'
            else:
                msg += '\t| ' + '{:.2e}'.format(norm(self.residual))
            msg += ' |      nan' * 4
            if self.num_comp_vars > 0:
                msg += ' |      nan'

        print(msg)

    def print_iterinfo_tail(self):
        """Print tail of convergence progress."""
        msg = (
            'Total iterations: ' + str(self.iter + 1) + ', Calculation '
            'time: ' + str(round(self.end_time - self.start_time, 1)) +
            ' s, Iterations per second: ')
        ips = 'inf'
        if self.end_time != self.start_time:
            ips = str(round(
                (self.iter + 1) / (self.end_time - self.start_time), 2))
        msg += ips
        logging.debug(msg)

        if self.iterinfo:
            if self.num_comp_vars == 0:
                print('-' * 8 + '+----------' * 4 + '+' + '-' * 9)
            else:
                print('-' * 8 + '+----------' * 5 + '+' + '-' * 9)
            print(msg)

    def matrix_inversion(self):
        """Invert matrix of derivatives and caluclate increment."""
        self.lin_dep = True
        try:
            # Let the matrix inversion be computed by the GPU if use_cuda in
            # global_vars.py is true.
            if self.use_cuda:
                self.increment = cu.asnumpy(cu.dot(
                    cu.linalg.inv(cu.asarray(self.jacobian)),
                    -cu.asarray(self.residual)))
            else:
                self.increment = np.linalg.inv(
                    self.jacobian).dot(-self.residual)
            self.lin_dep = False
        except np.linalg.linalg.LinAlgError:
            self.increment = self.residual * 0

    def solve_control(self):
        r"""
        Control iteration step of the newton algorithm.

        - Calculate the residual value for each equation
        - Calculate the jacobian matrix
        - Calculate new values for variables
        - Restrict fluid properties to value ranges
        - Check component parameters for consistency
        """
        self.solve_components()
        self.solve_busses()
        self.solve_connections()
        self.matrix_inversion()

        # check for linear dependency
        if self.lin_dep:
            return

        # add the increment
        i = 0
        for c in self.conns.index:
            # mass flow, pressure and enthalpy
            if not c.m.val_set:
                c.m.val_SI += self.increment[i * (self.num_conn_vars)]
            if not c.p.val_set:
                # this prevents negative pressures
                relax = max(1, -self.increment[i * (self.num_conn_vars) + 1] /
                            (0.5 * c.p.val_SI))
                c.p.val_SI += self.increment[
                    i * (self.num_conn_vars) + 1] / relax
            if not c.h.val_set:
                c.h.val_SI += self.increment[i * (self.num_conn_vars) + 2]

            # fluid vector (only if number of fluids is greater than 1)
            if len(self.fluids) > 1:
                j = 0
                for fluid in self.fluids:
                    # add increment
                    if not c.fluid.val_set[fluid]:
                        c.fluid.val[fluid] += (
                                self.increment[
                                    i * (self.num_conn_vars) + 3 + j])

                    # keep mass fractions within [0, 1]
                    if c.fluid.val[fluid] < err:
                        c.fluid.val[fluid] = 0
                    elif c.fluid.val[fluid] > 1 - err:
                        c.fluid.val[fluid] = 1

                    j += 1

            # check the fluid properties for physical ranges
            self.solve_check_props(c)
            i += 1

        # increment for the custom variables
        if self.num_comp_vars > 0:
            sum_c_var = 0
            for cp in self.comps.index:
                for var in cp.vars.keys():
                    pos = var.var_pos

                    # add increment
                    var.val += self.increment[
                        self.num_conn_vars * len(self.conns) + sum_c_var + pos]

                    # keep value within specified value range
                    if var.val < var.min_val:
                        var.val = var.min_val
                    elif var.val > var.max_val:
                        var.val = var.max_val

                sum_c_var += cp.num_vars

        # second property check for first three iterations without an init_file
        if self.iter < 3:
            for cp in self.comps.index:
                cp.convergence_check()

            for c in self.conns.index:
                self.solve_check_props(c)

    def property_range_message(self, c, prop):
        r"""
        Return debugging message for fluid property range adjustments.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to check fluid properties.

        prop : str
            Fluid property.

        Returns
        -------
        msg : str
            Debugging message.
        """
        msg = (
            self.props[prop][0].upper() + self.props[prop][1:] +
            ' out of fluid property range at connection ' + c.label +
            ' adjusting value to ' + str(c.get_attr(prop).val_SI) +
            ' ' + self.SI_units[prop] + '.')
        return msg

    def solve_check_props(self, c):
        r"""
        Check for invalid fluid property values.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to check fluid properties.
        """
        fl = hlp.single_fluid(c.fluid.val)

        if fl is not None:
            # pressure
            if c.p.val_SI < fp.Memorise.value_range[fl][0] and not c.p.val_set:
                c.p.val_SI = fp.Memorise.value_range[fl][0]
                logging.debug(self.property_range_message(c, 'p'))
            elif (c.p.val_SI > fp.Memorise.value_range[fl][1] and
                  not c.p.val_set):
                c.p.val_SI = fp.Memorise.value_range[fl][1]
                logging.debug(self.property_range_message(c, 'p'))

            # enthalpy
            try:
                hmin = fp.h_pT(
                    c.p.val_SI, fp.Memorise.value_range[fl][2] * 1.001, fl)
            except ValueError:
                f = 1.05
                hmin = fp.h_pT(
                    c.p.val_SI, fp.Memorise.value_range[fl][2] * f, fl)

            T = fp.Memorise.value_range[fl][3]
            while True:
                try:
                    hmax = fp.h_pT(c.p.val_SI, T, fl)
                    break
                except ValueError as e:
                    T *= 0.99
                    if T < fp.Memorise.value_range[fl][2]:
                        raise ValueError(e)

            if c.h.val_SI < hmin and not c.h.val_set:
                if hmin < 0:
                    c.h.val_SI = hmin * 0.9999
                else:
                    c.h.val_SI = hmin * 1.0001
                logging.debug(self.property_range_message(c, 'h'))

            elif c.h.val_SI > hmax and not c.h.val_set:
                c.h.val_SI = hmax * 0.9999
                logging.debug(self.property_range_message(c, 'h'))

            if ((c.Td_bp.val_set or c.state.is_set) and
                    not c.h.val_set and self.iter < 3):
                if (c.Td_bp.val_SI > 0 or
                        (c.state.val == 'g' and c.state.is_set)):
                    h = fp.h_mix_pQ(c.to_flow(), 1)
                    if c.h.val_SI < h:
                        c.h.val_SI = h * 1.01
                        logging.debug(self.property_range_message(c, 'h'))
                elif (c.Td_bp.val_SI < 0 or
                      (c.state.val == 'l' and c.state.is_set)):
                    h = fp.h_mix_pQ(c.to_flow(), 0)
                    if c.h.val_SI > h:
                        c.h.val_SI = h * 0.99
                        logging.debug(self.property_range_message(c, 'h'))

        elif self.iter < 4 and not c.good_starting_values:
            # pressure
            if c.p.val_SI <= self.p_range_SI[0] and not c.p.val_set:
                c.p.val_SI = self.p_range_SI[0]
                logging.debug(self.property_range_message(c, 'p'))

            elif c.p.val_SI >= self.p_range_SI[1] and not c.p.val_set:
                c.p.val_SI = self.p_range_SI[1]
                logging.debug(self.property_range_message(c, 'p'))

            # enthalpy
            if c.h.val_SI < self.h_range_SI[0] and not c.h.val_set:
                c.h.val_SI = self.h_range_SI[0]
                logging.debug(self.property_range_message(c, 'h'))

            elif c.h.val_SI > self.h_range_SI[1] and not c.h.val_set:
                c.h.val_SI = self.h_range_SI[1]
                logging.debug(self.property_range_message(c, 'h'))

            # temperature
            if c.T.val_set and not c.h.val_set:
                self.solve_check_temperature(c)

        # mass flow
        if c.m.val_SI <= self.m_range_SI[0] and not c.m.val_set:
            c.m.val_SI = self.m_range_SI[0]
            logging.debug(self.property_range_message(c, 'm'))

        elif c.m.val_SI >= self.m_range_SI[1] and not c.m.val_set:
            c.m.val_SI = self.m_range_SI[1]
            logging.debug(self.property_range_message(c, 'm'))

    def solve_check_temperature(self, c):
        r"""
        Check if temperature is within user specified limits.

        Parameters
        ----------
        c : tespy.connections.connection.Connection
            Connection to check fluid properties.
        """
        flow = c.to_flow()
        Tmin = max(
            [fp.Memorise.value_range[f][2] for
             f in flow[3].keys() if flow[3][f] > err]
        ) + 100
        Tmax = min(
            [fp.Memorise.value_range[f][3] for
             f in flow[3].keys() if flow[3][f] > err]
        ) - 100
        hmin = fp.h_mix_pT(flow, Tmin)
        hmax = fp.h_mix_pT(flow, Tmax)

        if c.h.val_SI < hmin:
            c.h.val_SI = hmin
            logging.debug(self.property_range_message(c, 'h'))

        if c.h.val_SI > hmax:
            c.h.val_SI = hmax
            logging.debug(self.property_range_message(c, 'h'))

    def solve_components(self):
        r"""
        Calculate the residual and derivatives of component equations.

        - Iterate through components in network to get residuals and
          derivatives.
        - Place residual values in residual value vector of the network.
        - Place partial derivatives in jacobian matrix of the network.
        """
        # fetch component equation residuals and component partial derivatives
        sum_eq = 0
        sum_c_var = 0
        for cp in self.comps.index:

            indices = []
            for c in cp.conn_loc:
                start = c * self.num_conn_vars
                end = (c + 1) * self.num_conn_vars
                indices += [np.arange(start, end)]

            cp.equations()
            cp.derivatives(self.increment_filter[np.array(indices)])

            self.residual[sum_eq:sum_eq + cp.num_eq] = cp.residual
            deriv = cp.jacobian

            if deriv is not None:
                i = 0
                # place derivatives in jacobian matrix
                for loc in cp.conn_loc:
                    coll_s = loc * self.num_conn_vars
                    coll_e = (loc + 1) * self.num_conn_vars
                    self.jacobian[
                        sum_eq:sum_eq + cp.num_eq, coll_s:coll_e] = deriv[:, i]
                    i += 1

                # derivatives for custom variables
                for j in range(cp.num_vars):
                    coll = self.num_vars - self.num_comp_vars + sum_c_var
                    self.jacobian[sum_eq:sum_eq + cp.num_eq, coll] = (
                        deriv[:, i + j, :1].transpose()[0])
                    sum_c_var += 1

                sum_eq += cp.num_eq
            cp.it += 1

    def solve_connections(self):
        r"""
        Calculate the residual and derivatives of connection equations.

        - Iterate through connections in network to get residuals and
          derivatives.
        - Place residual values in residual value vector of the network.
        - Place partial derivatives in jacobian matrix of the network.

        Note
        ----
        **Equations**

        **mass flow, pressure and enthalpy**

        .. math::
            val = 0

        **temperatures**

        .. math::
            val = T_{j} - T \left( p_{j}, h_{j}, fluid_{j} \right)

        **volumetric flow**

        .. math::
            val = \dot{V}_{j} - v \left( p_{j}, h_{j} \right) \cdot \dot{m}_j

        **superheating or subcooling** *Works with pure fluids only!*

        .. math::
            val = T_{j} - td_{bp} - T_{bp}\left( p_{j}, fluid_{j} \right)

            \text{td: temperature difference, bp: boiling point}

        **vapour mass fraction** *Works with pure fluids only!*

        .. math::
            val = h_{j} - h \left( p_{j}, x_{j}, fluid_{j} \right)

        **Referenced values**

        **mass flow, pressure and enthalpy**

        .. math::
            val = x_{j} - x_{j,ref} \cdot a + b

        **temperatures**

        .. math::
            val = T \left( p_{j}, h_{j}, fluid_{j} \right) -
            T \left( p_{j}, h_{j}, fluid_{j} \right) \cdot a + b

        **Derivatives**

        **mass flow, pressure and enthalpy**

        .. math::

            J\left(\frac{\partial f_{i}}{\partial m_{j}}\right) = 1\\
            \text{for equation i, connection j}\\
            \text{pressure and enthalpy analogously}

        **temperatures**

        .. math::

            J\left(\frac{\partial f_{i}}{\partial p_{j}}\right) =
            -\frac{\partial T_{j}}{\partial p_{j}}\\
            J(\left(\frac{\partial f_{i}}{\partial h_{j}}\right) =
            -\frac{\partial T_{j}}{\partial h_{j}}\\
            J\left(\frac{\partial f_{i}}{\partial fluid_{j,k}}\right) =
            - \frac{\partial T_{j}}{\partial fluid_{j,k}}

            \forall k \in \text{fluid components}\\
            \text{for equation i, connection j}

        **volumetric flow**

        .. math::

            J\left(\frac{\partial f_{i}}{\partial m_{j}}\right) =
            -v \left( p_{j}, h_{j} \right)\\
            J\left(\frac{\partial f_{i}}{\partial p_{j}}\right) =
            -\frac{\partial v_{j}}{\partial p_{j}} \cdot \dot{m}_j\\
            J(\left(\frac{\partial f_{i}}{\partial h_{j}}\right) =
            -\frac{\partial v_{j}}{\partial h_{j}} \cdot \dot{m}_j\\

            \forall k \in \text{fluid components}\\
            \text{for equation i, connection j}

        **superheating or subcooling** *Works with pure fluids only!*

        .. math::

            J\left(\frac{\partial f_{i}}{\partial p_{j}}\right) =
            \frac{\partial T \left( p_{j}, h_{j}, fluid_{j} \right)}
            {\partial p_{j}} -
            \frac{\partial T_{bp} \left( p_{j}, fluid_{j} \right)}
            {\partial p_{j}} \\
            J\left(\frac{\partial f_{i}}{\partial h_{j}}\right) =
            \frac{\partial T \left( p_{j}, h_{j}, fluid_{j} \right)}
            {\partial h_{j}}\\

            \text{for equation i, connection j}\\
            \text{td: temperature difference, bp: boiling point}

        **vapour mass fraction** *Works with pure fluids only!*

        .. math::

            J\left(\frac{\partial f_{i}}{\partial p_{j}}\right) =
            -\frac{\partial h \left( p_{j}, x_{j}, fluid_{j} \right)}
            {\partial p_{j}}\\
            J\left(\frac{\partial f_{i}}{\partial h_{j}}\right) = 1\\
            \text{for equation i, connection j, x: vapour mass fraction}

        **Referenced values**

        **mass flow, pressure and enthalpy**

        .. math::
            J\left(\frac{\partial f_{i}}{\partial m_{j}}\right) = 1\\
            J\left(\frac{\partial f_{i}}{\partial m_{j,ref}}\right) = - a\\
            \text{for equation i, connection j}\\
            \text{pressure and enthalpy analogously}

        **temperatures**

        .. math::
            J\left(\frac{\partial f_{i}}{\partial p_{j}}\right) =
            \frac{dT_{j}}{dp_{j}}\\
            J\left(\frac{\partial f_{i}}{\partial h_{j}}\right) =
            \frac{dT_{j}}{dh_{j}}\\
            J\left(\frac{\partial f_{i}}{\partial fluid_{j,k}}\right) =
            \frac{dT_{j}}{dfluid_{j,k}}
            \; , \forall k \in \text{fluid components}\\
            J\left(\frac{\partial f_{i}}{\partial p_{j,ref}}\right) =
            \frac{dT_{j,ref}}{dp_{j,ref}} \cdot a \\
            J\left(\frac{\partial f_{i}}{\partial h_{j,ref}}\right) =
            \frac{dT_{j,ref}}{dh_{j,ref}} \cdot a \\
            J\left(\frac{\partial f_{i}}{\partial fluid_{j,k,ref}}\right) =
            \frac{dT_{j}}{dfluid_{j,k,ref}} \cdot a
            \; , \forall k \in \text{fluid components}\\
            \text{for equation i, connection j}
        """
        primary_vars = {'m': 0, 'p': 1, 'h': 2}
        k = self.num_comp_eq
        for c in self.conns.index:
            flow = c.to_flow()
            col = c.conn_loc * self.num_conn_vars

            # referenced mass flow, pressure or enthalpy
            for var, pos in primary_vars.items():
                if c.get_attr(var).ref_set:
                    ref = c.get_attr(var).ref
                    ref_col = ref.obj.conn_loc * self.num_conn_vars
                    self.residual[k] = (
                        c.get_attr(var).val_SI - (
                            ref.obj.get_attr(var).val_SI * ref.f + ref.d))
                    self.jacobian[k, col + pos] = 1
                    self.jacobian[k, ref_col + pos] = -c.get_attr(var).ref.f
                    k += 1

            # temperature
            if c.T.val_set:
                self.residual[k] = c.T.val_SI - fp.T_mix_ph(
                    flow, T0=c.T.val_SI)

                self.jacobian[k, col + 1] = (
                    -fp.dT_mix_dph(flow, T0=c.T.val_SI))
                self.jacobian[k, col + 2] = (
                    -fp.dT_mix_pdh(flow, T0=c.T.val_SI))
                if len(self.fluids) != 1:
                    col_s = c.conn_loc * self.num_conn_vars + 3
                    col_e = (c.conn_loc + 1) * self.num_conn_vars
                    if not all(self.increment_filter[col_s:col_e]):
                        self.jacobian[k, col_s:col_e] = -fp.dT_mix_ph_dfluid(
                            flow, T0=c.T.val_SI)
                k += 1

            # referenced temperature
            if c.T.ref_set:
                ref = c.T.ref
                flow_ref = ref.obj.to_flow()
                ref_col = ref.obj.conn_loc * self.num_conn_vars
                self.residual[k] = fp.T_mix_ph(flow, T0=c.T.val_SI) - (
                    fp.T_mix_ph(flow_ref, T0=ref.obj.T.val_SI) *
                    ref.f + ref.d)

                self.jacobian[k, col + 1] = (
                    fp.dT_mix_dph(flow, T0=c.T.val_SI))
                self.jacobian[k, col + 2] = (
                    fp.dT_mix_pdh(flow, T0=c.T.val_SI))

                self.jacobian[k, ref_col + 1] = -(
                    fp.dT_mix_dph(flow_ref, T0=ref.obj.T.val_SI) * ref.f)
                self.jacobian[k, ref_col + 2] = -(
                    fp.dT_mix_pdh(flow_ref, T0=ref.obj.T.val_SI) * ref.f)

                # dT / dFluid
                if len(self.fluids) != 1:
                    col_s = c.conn_loc * self.num_conn_vars + 3
                    col_e = (c.conn_loc + 1) * self.num_conn_vars
                    ref_col_s = ref.obj.conn_loc * self.num_conn_vars + 3
                    ref_col_e = (ref.obj.conn_loc + 1) * self.num_conn_vars
                    if not all(self.increment_filter[col_s:col_e]):
                        self.jacobian[k, col_s:col_e] = (
                            fp.dT_mix_ph_dfluid(flow, T0=c.T.val_SI))
                    if not all(self.increment_filter[ref_col_s:ref_col_e]):
                        self.jacobian[k, ref_col_s:ref_col_e] = -(
                            fp.dT_mix_ph_dfluid(flow_ref, T0=ref.obj.T.val_SI))
                k += 1

            # saturated steam fraction
            if c.x.val_set:
                if (np.absolute(self.residual[k]) > err ** 2 or
                        self.iter % 2 == 0 or self.always_all_equations):
                    self.residual[k] = c.h.val_SI - (
                        fp.h_mix_pQ(flow, c.x.val_SI))
                if not self.increment_filter[col + 1]:
                    self.jacobian[k, col + 1] = -(
                        fp.dh_mix_dpQ(flow, c.x.val_SI))
                self.jacobian[k, col + 2] = 1
                k += 1

            # volumetric flow
            if c.v.val_set:
                if (np.absolute(self.residual[k]) > err ** 2 or
                        self.iter % 2 == 0 or self.always_all_equations):
                    self.residual[k] = (
                        c.v.val_SI - fp.v_mix_ph(flow, T0=c.T.val_SI) *
                        c.m.val_SI)
                self.jacobian[k, col] = -fp.v_mix_ph(flow, T0=c.T.val_SI)
                self.jacobian[k, col + 1] = -(
                    fp.dv_mix_dph(flow, T0=c.T.val_SI) * c.m.val_SI)
                self.jacobian[k, col + 2] = -(
                    fp.dv_mix_pdh(flow, T0=c.T.val_SI) * c.m.val_SI)
                k += 1

            # temperature difference to boiling point
            if c.Td_bp.val_set:
                if (np.absolute(self.residual[k]) > err ** 2 or
                        self.iter % 2 == 0 or self.always_all_equations):
                    self.residual[k] = (
                        fp.T_mix_ph(flow, T0=c.T.val_SI) - c.Td_bp.val_SI -
                        fp.T_bp_p(flow))
                if not self.increment_filter[col + 1]:
                    self.jacobian[k, col + 1] = (
                        fp.dT_mix_dph(flow, T0=c.T.val_SI) - fp.dT_bp_dp(flow))
                if not self.increment_filter[col + 2]:
                    self.jacobian[k, col + 2] = fp.dT_mix_pdh(
                        flow, T0=c.T.val_SI)
                k += 1

            # fluid composition balance
            if c.fluid.balance:
                j = 0
                res = 1
                for f in self.fluids:
                    res -= c.fluid.val[f]
                    self.jacobian[k, c.conn_loc + 3 + j] = -1
                    j += 1

                self.residual[k] = res
                k += 1

        # equations and derivatives for specified primary variables are static
        if self.iter == 0:
            for c in self.conns.index:
                col = c.conn_loc * self.num_conn_vars

                # specified mass flow, pressure and enthalpy
                for var, pos in primary_vars.items():
                    if c.get_attr(var).val_set:
                        self.residual[k] = 0
                        self.jacobian[k, col + pos] = 1
                        k += 1

                j = 0
                # specified fluid mass fraction
                for f in self.fluids:
                    if c.fluid.val_set[f]:
                        self.jacobian[k, col + 3 + j] = 1
                        k += 1
                    j += 1

    def solve_busses(self):
        r"""
        Calculate the equations and the partial derivatives for the busses.

        - Iterate through busses in network to get residuals and derivatives.
        - Place residual values in residual value vector of the network.
        - Place partial derivatives in jacobian matrix of the network.
        """
        row = self.num_comp_eq + self.num_conn_eq
        for bus in self.busses.values():
            if bus.P.is_set:
                P_res = 0
                for cp in bus.comps.index:

                    P_res -= cp.calc_bus_value(bus)
                    deriv = -cp.bus_deriv(bus)

                    j = 0
                    for loc in cp.conn_loc:
                        # start collumn index
                        coll_s = loc * self.num_conn_vars
                        # end collumn index
                        coll_e = (loc + 1) * self.num_conn_vars
                        self.jacobian[row, coll_s:coll_e] = deriv[:, j]
                        j += 1

                self.residual[row] = bus.P.val + P_res
                row += 1

    def postprocessing(self):
        r"""Calculate connection, bus and component parameters."""
        # connections
        for c in self.conns.index:
            flow = c.to_flow()
            c.good_starting_values = True
            c.T.val_SI = fp.T_mix_ph(flow, T0=c.T.val_SI)
            fluid = hlp.single_fluid(c.fluid.val)
            if (fluid is None and
                    abs(
                        fp.h_mix_pT(flow, c.T.val_SI) - c.h.val_SI
                    ) > err ** .5):
                c.T.val_SI = np.nan
                c.vol.val_SI = np.nan
                c.v.val_SI = np.nan
                c.s.val_SI = np.nan
                msg = (
                    'Could not find a feasible value for mixture temperature '
                    'at connection ' + c.label + '. The values for '
                    'temperature, specific volume, volumetric flow and '
                    'entropy are set to nan.')
                logging.warning(msg)

            else:
                c.vol.val_SI = fp.v_mix_ph(flow, T0=c.T.val_SI)
                c.v.val_SI = c.vol.val_SI * c.m.val_SI
                c.s.val_SI = fp.s_mix_ph(flow, T0=c.T.val_SI)
                if fluid is not None and not c.x.val_set:
                    c.x.val_SI = fp.Q_ph(c.p.val_SI, c.h.val_SI, fluid)

            for prop in self.props.keys():
                c.get_attr(prop).val = self.convert_from_SI(
                    prop, c.get_attr(prop).val_SI, c.get_attr(prop).unit)

            c.m.val0 = c.m.val
            c.p.val0 = c.p.val
            c.h.val0 = c.h.val
            c.fluid.val0 = c.fluid.val.copy()

        # components
        for cp in self.comps.index:
            cp.calc_parameters()
            cp.entropy_balance()

        # busses
        for b in self.busses.values():
            b.P.val = 0
            for cp in b.comps.index:
                # get components bus func value
                val = cp.calc_bus_value(b)
                b.P.val += val
                # save as reference value
                if self.mode == 'design':
                    if b.comps.loc[cp, 'base'] == 'component':
                        b.comps.loc[cp, 'P_ref'] = (
                            val / abs(b.comps.loc[cp, 'char'].evaluate(1)))
                    else:
                        b.comps.loc[cp, 'P_ref'] = val

        msg = 'Postprocessing complete.'
        logging.info(msg)

    def exergy_analysis(self, pamb, Tamb, E_F, E_P, E_L=[],
                        internal_busses=[]):
        r"""Perform exergy analysis.

        - Calculate the values of physical exergy on all connections.
        - Calculate exergy balance for all components. The individual exergy
          balance methods are documented in the API-documentation of the
          respective components.

          - Components for which no exergy balance has yet been implemented,
            :code:`nan` (not defined) is assigned for fuel and product
            exergy as well as exergy destruction and exergetic efficiency.
          - Dissipative components do not have product exergy (:code:`nan`) per
            definition.

        - Calculate network fuel exergy and product exergy from data provided
          from the busses passed to this method.
        - Component fuel and product exergy of components passed within the
          busses of :code:`E_F`, :code:`E_P` and :code:`internal_busses` are
          adjusted to consider the bus conversion factor, too.
        - Calculate network exergetic efficiency.
        - Calculate exergy destruction ratios for components.

          - :math:`y_\mathrm{D}` compare the rate of exergy destruction in a
            component to the exergy rate of the fuel provided to the overall
            system.
          - :math:`y^*_\mathrm{D}` compare the component exergy destruction
            rate to the total exergy destruction rate within the system.

        Parameters
        ----------
        pamb : float
            Ambient pressure in network's pressure unit.

        Tamb : float
            Ambient temperature in network's temperature unit.

        E_F : float
            List containing busses which represent fuel exergy input of the
            network, e.g. heat exchangers of the steam generator.

        E_P : list
            List containing busses which represent exergy production of the
            network, e.g. the motors and generators of a power plant.

        E_L : list
            List containing busses which represent exergy loss streams of the
            network to the ambient, e.g. flue gases of a gas turbine.

        internal_busses : list
            Optional: List containing internal busses that represent exergy
            transfer within your network but neither exergy production or
            exergy fuel, e.g. a steam turbine driven feed water pump. The
            conversion factors of the bus are applied to calculate exergy
            destruction which is allocated to the respective components.

        Note
        ----
        The nomenclature of the variables used in the exergy analysis is
        according to :cite:`Tsatsaronis2007`.

        .. math::

            \begin{split}
            E_{\mathrm{D},comp} = E_{\mathrm{F},comp} - E_{\mathrm{P},comp}
            \;& \\
            \varepsilon_{\mathrm{comp}} =
            \frac{E_{\mathrm{P},comp}}{E_{\mathrm{F},comp}} \;& \\
            E_{\mathrm{D}} = \sum_{comp} E_{\mathrm{D},comp} \;&
            \forall comp \in \text{ network components}\\
            E_{\mathrm{P}} = \sum_{comp} E_{\mathrm{P},comp} \;&
            \forall comp \in
            \text{ components of busses in E\_P if 'base': 'component'}\\
            E_{\mathrm{P}} = E_{\mathrm{P}} - \sum_{comp} E_{\mathrm{F},comp}
            \;& \forall comp \in
            \text{ components of busses in E\_P if 'base': 'bus'}\\
            E_{\mathrm{F}} = \sum_{comp} E_{\mathrm{F},comp} \;&
            \forall comp \in
            \text{ components of busses in E\_F if 'base': 'bus'}\\
            E_{\mathrm{F}} = E_{\mathrm{F}} - \sum_{comp} E_{\mathrm{P},comp}
            \;& \forall comp \in
            \text{ components of busses in E\_F if 'base': 'component'}\\
            E_{\mathrm{L}} = \sum_{comp} E_{\mathrm{D},comp} \;&
            \forall comp \in
            \text{ sinks of network components if parameter exergy='loss'}
            \end{split}

        The exergy balance of the network must be closed, meaning fuel exergy
        minus product exergy, exergy destruction and exergy losses must be
        zero (:math:`\Delta E_\text{max}=0.001`). If the balance is violated a
        warning message is prompted.

        .. math::

            |E_{\text{F}} - E_{\text{P}} - E_{\text{L}} - E_{\text{D}}| \leq
            \Delta E_\text{max}\\

            \varepsilon = \frac{E_{\text{P}}}{E_{\text{F}}}

            y_{\text{D},comp} =
            \frac{\dot{E}_{\text{D},comp}}{\dot{E}_{\text{F}}}\\
            y^*_{\text{D},comp} =
            \frac{\dot{E}_{\text{D},comp}}{\dot{E}_{\text{D}}}

        Example
        -------
        In this example a simple clausius rankine cycle is set up and an
        exergy analysis is performed after simulation of the power plant.
        Start by defining ambient state and genereral network setup.

        >>> from tespy.components import (CycleCloser, HeatExchangerSimple,
        ... Merge, Splitter, Valve, Compressor, Pump, Turbine)
        >>> from tespy.connections import Bus
        >>> from tespy.connections import Connection
        >>> from tespy.networks import Network

        >>> Tamb = 20
        >>> pamb = 1
        >>> fluids = ['water']
        >>> nw = Network(fluids=fluids)
        >>> nw.set_attr(p_unit='bar', T_unit='C', h_unit='kJ / kg',
        ... iterinfo=False)

        In order to show all functionalities available we use a feed water pump
        that is not driven electrically by a motor but instead internally by
        an own steam turbine. Therefore we split up the live steam from the
        steam generator and merge the streams after both steam turbines. For
        simplicity the steam generator and the condenser are modeled as simple
        heat exchangers.

        >>> cycle_close = CycleCloser('cycle closer')
        >>> splitter1 = Splitter('splitter 1')
        >>> merge1 = Merge('merge 1')
        >>> turb = Turbine('turbine')
        >>> fwp_turb = Turbine('feed water pump turbine')
        >>> condenser = HeatExchangerSimple('condenser')
        >>> fwp = Pump('pump')
        >>> steam_generator = HeatExchangerSimple('steam generator')

        >>> fs_in = Connection(cycle_close, 'out1', splitter1, 'in1')
        >>> fs_fwpt = Connection(splitter1, 'out1', fwp_turb, 'in1')
        >>> fs_t = Connection(splitter1, 'out2', turb, 'in1')
        >>> fwpt_ws = Connection(fwp_turb, 'out1', merge1, 'in1')
        >>> t_ws = Connection(turb, 'out1', merge1, 'in2')
        >>> ws = Connection(merge1, 'out1', condenser, 'in1')
        >>> cond = Connection(condenser, 'out1', fwp, 'in1')
        >>> fw = Connection(fwp, 'out1', steam_generator, 'in1')
        >>> fs_out = Connection(steam_generator, 'out1', cycle_close, 'in1')
        >>> nw.add_conns(fs_in, fs_fwpt, fs_t, fwpt_ws, t_ws, ws, cond,
        ... fw, fs_out)

        Next step is to set up the busses to later pass them according to the
        convetions in the list below:

        - E_F for fuel exergy
        - E_P for product exergy
        - internal_busses for internal energy transport
        - E_L for exergy loss streams to the ambient (sources and sinks go
          here, in case you use e.g. flue gases or air input)

        The first bus is for output power, which is only represented by the
        main steam turbine. The efficiency is set to 0.97. This bus will
        represent the product exergy.

        >>> power = Bus('power_output')
        >>> power.add_comps({'comp': turb, 'char': 0.97})

        The second bus is for driving the feed water pump. The total power of
        this bus is specified to be 0 in order to make sure, the power genrated
        by the secondary steam turbine is transferred to the feed water pump.
        For mechanical efficiency we choose 0.985 for both components, but
        we need to make sure, the :code:`'base'` of the feed water pump is
        :code:`'bus'` as the energy from the turbine drives the feed water
        pump.

        >>> fwp_power = Bus('feed water pump power', P=0)
        >>> fwp_power.add_comps(
        ... {'comp': fwp_turb, 'char': 0.985},
        ... {'comp': fwp, 'char': 0.985, 'base': 'bus'})

        The fuel exergy is the exergy input into the network which is
        represented by the heat input bus. Here again, as we have an energy
        input from outside of the network, the :code:`'base'` keyword must be
        specified to :code:`'bus'`.

        >>> heat = Bus('heat_input')
        >>> heat.add_comps({'comp': steam_generator, 'base': 'bus'})
        >>> nw.add_busses(power, fwp_power, heat)

        After setting up the busses, we specify the parameters for components
        and connections and start the simulation.

        >>> turb.set_attr(eta_s=0.9)
        >>> fwp_turb.set_attr(eta_s=0.87)
        >>> condenser.set_attr(pr=0.98)
        >>> fwp.set_attr(eta_s=0.75)
        >>> steam_generator.set_attr(pr=0.89)
        >>> fs_in.set_attr(m=10, p=120, T=600, fluid={'water': 1})
        >>> cond.set_attr(T=Tamb + 3, x=0)
        >>> nw.solve('design')

        To evaluate the exergy balance of the network, we simply call the
        :py:meth:`tespy.networks.network.Network.exergy_analysis` method
        passing the respective busses as well as the ambient state. To print
        the results you can subsequently use the
        :py:meth:`tespy.networks.network.Network.print_exergy_analysis`
        method. The exergy balance should be closed, if you set up your network
        analysis. If not, an error is prompted.

        >>> nw.exergy_analysis(pamb=pamb, Tamb=Tamb,
        ... E_F=[heat], E_P=[power], internal_busses=[fwp_power])
        >>> abs(round(nw.E_F - nw.E_P - nw.E_L - nw.E_D, 3))
        0.0
        >>> ();nw.print_exergy_analysis();() # doctest: +ELLIPSIS
        (...)

        The component exergy and connection exergy data are stored as
        dataframes and therefore accessible for further investigation.

        >>> components = nw.component_exergy_data
        >>> connections = nw.connection_exergy_data

        """
        pamb_SI = self.convert_to_SI('p', pamb, self.p_unit)
        Tamb_SI = self.convert_to_SI('T', Tamb, self.T_unit)

        self.component_exergy_data = pd.DataFrame(
            columns=['label', 'E_F', 'E_P', 'E_D', 'epsilon', 'y_Dk', 'y*_Dk'])

        self.connection_exergy_data = pd.DataFrame(columns=['e_PH', 'E_PH'])

        self.E_P = 0
        self.E_F = 0
        self.E_D = 0
        self.E_L = 0

        if len(E_F) == 0:
            msg = ('Missing fuel exergy E_F of network.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)
        elif len(E_P) == 0:
            msg = ('Missing product exergy E_P of network.')
            logging.error(msg)
            raise hlp.TESPyNetworkError(msg)

        # physical exergy of connections
        for conn in self.conns.index:
            conn.get_physical_exergy(pamb_SI, Tamb_SI)
            self.connection_exergy_data.loc[conn.label] = [
                conn.ex_physical, conn.Ex_physical]

        # exergy balance of components

        for cp in self.comps.index:
            cp.exergy_balance(Tamb_SI)
            self.E_D += cp.E_D

            self.component_exergy_data.loc[cp.label] = [
                cp.label, cp.E_F, cp.E_P, cp.E_D, cp.epsilon, np.nan, np.nan]

            cp_on_num_busses = 0
            for b in E_F + E_P + internal_busses + E_L:
                if cp in b.comps.index:
                    if cp_on_num_busses > 0:
                        msg = (
                            'The component ' + cp.label + ' is on multiple '
                            'busses in the exergy analysis. Make sure that no '
                            'component is connected to more than one of the '
                            'busses passed to the exergy_analysis method.')
                        logging.error(msg)
                        raise hlp.TESPyNetworkError(msg)

                    if b.comps.loc[cp, 'base'] == 'bus':
                        cp_E_P = cp.E_bus
                        cp_E_F = cp.E_bus / cp.calc_bus_efficiency(b)
                    else:
                        cp_E_P = cp.E_bus * cp.calc_bus_efficiency(b)
                        cp_E_F = cp.E_bus

                    cp_E_D = cp_E_F - cp_E_P
                    self.E_D += cp_E_D
                    epsilon = cp_E_P / cp_E_F

                    if b in E_F:
                        if b.comps.loc[cp, 'base'] == 'bus':
                            self.E_F += cp_E_F
                        else:
                            self.E_F -= cp_E_P
                    elif b in E_P:
                        if b.comps.loc[cp, 'base'] == 'bus':
                            self.E_P -= cp_E_F
                        else:
                            self.E_P += cp_E_P
                    elif b in E_L:
                        if b.comps.loc[cp, 'base'] == 'bus':
                            self.E_L -= cp_E_F
                        else:
                            self.E_L += cp_E_P

                    cp_on_num_busses += 1

                    label = cp.label + ' on bus ' + b.label
                    self.component_exergy_data.loc[label] = [
                        label, cp_E_F, cp_E_P, cp_E_D, epsilon, np.nan, np.nan]

        self.E_D = self.component_exergy_data['E_D'].sum()
        self.E_F = abs(self.E_F)
        self.E_P = abs(self.E_P)

        self.epsilon = self.E_P / self.E_F

        # calculate exergy destruction ratios for components/busses
        self.component_exergy_data['y_Dk'] = (
            self.component_exergy_data['E_D'] / self.E_F)
        self.component_exergy_data['y*_Dk'] = (
            self.component_exergy_data['E_D'] / self.E_D)

        residual = abs(self.E_F - self.E_P - self.E_L - self.E_D)
        if residual >= err ** 0.5:
            msg = (
                'The exergy balance of your network is not closed (residual '
                'value is ' + str(round(residual, 6)) + ', but should be '
                'smaller than 1e-3), you should check the component and '
                'network exergy data and check, if network is properly setup '
                'for the exergy analysis.')
            logging.warning(msg)

# %% printing and plotting

    def print_results(self, colored=True):
        r"""Print the calculations results to prompt."""

        for cp in self.comps['comp_type'].unique():
            df = self.comps[self.comps['comp_type'] == cp].copy()

            # gather parameters to print for components of type c
            cols = []
            for col, val in df.index[0].variables.items():
                if isinstance(val, dc_cp):
                    if val.get_attr('printout'):
                        cols += [col]

            # are there any parameters to print?
            if len(cols) > 0:
                for col in cols:
                    df[col] = df.apply(
                        Network.print_components, axis=1, args=(col, colored))

                df.drop(['comp_type'], axis=1, inplace=True)
                df.set_index('label', inplace=True)
                df.dropna(how='all', inplace=True)

                if len(df) > 0:
                    # printout with tabulate
                    print('##### RESULTS (' + cp + ') #####')
                    print(tabulate(
                        df, headers='keys', tablefmt='psql', floatfmt='.2e'))

        # connection properties
        df = pd.DataFrame(columns=[
            'm / (' + self.m_unit + ')',
            'p / (' + self.p_unit + ')',
            'h / (' + self.h_unit + ')',
            'T / (' + self.T_unit + ')'])
        for c in self.conns.index:
            if c.printout:
                row = (c.source.label + ':' + c.source_id + ' -> ' +
                       c.target.label + ':' + c.target_id)

                row_data = []
                for var in ['m', 'p', 'h', 'T']:
                    if c.get_attr(var).val_set and colored:
                        row_data += [
                            coloring['set'] + str(c.get_attr(var).val) +
                            coloring['end']
                        ]
                    else:
                        row_data += [str(c.get_attr(var).val)]

                df.loc[row] = row_data
        if len(df) > 0:
            print('##### RESULTS (connections) #####')
            print(
                tabulate(df, headers='keys', tablefmt='psql', floatfmt='.3e'))

        for b in self.busses.values():
            df = pd.DataFrame(columns=[
                'component', 'comp value', 'bus value', 'efficiency'])
            if b.printout:
                df['cp'] = b.comps.index
                df['base'] = b.comps['base'].values
                df['component'] = df['cp'].apply(lambda x: x.label)
                df['bus value'] = df['cp'].apply(lambda x: x.calc_bus_value(b))
                df['efficiency'] = df['cp'].apply(
                    lambda x: x.calc_bus_efficiency(b))
                df.loc[df['base'] == 'component', 'comp value'] = (
                    df['bus value'] / df['efficiency'])
                df.loc[df['base'] == 'bus', 'comp value'] = (
                    df['bus value'] * df['efficiency'])
                df.drop(['cp', 'base'], axis=1, inplace=True)
                df.loc['total'] = df.sum()
                df.loc['total', 'efficiency'] = np.nan
                df.loc['total', 'component'] = 'total'
                df.set_index('component', inplace=True)
                print('##### RESULTS (' + b.label + ') #####')
                print(tabulate(df, headers='keys', tablefmt='psql',
                               floatfmt='.3e'))

    def print_components(c, *args):
        param, colored = args
        if c.name.printout:
            val = float(c.name.get_attr(param).val)
            if not colored:
                return str(val)
            # else part
            if (val < c.name.get_attr(param).min_val - err or
                    val > c.name.get_attr(param).max_val + err):
                return coloring['err'] + ' ' + str(val) + ' ' + coloring['end']
            if c.name.get_attr(args[0]).is_var:
                return coloring['var'] + ' ' + str(val) + ' ' + coloring['end']
            if c.name.get_attr(args[0]).is_set:
                return coloring['set'] + ' ' + str(val) + ' ' + coloring['end']
            return str(val)
        else:
            return np.nan

    def print_connection_exergy_data(self):
        r"""Print the calculations results of the (specific) physical exergy of
        the connections to prompt.
        """
        df = pd.DataFrame(columns=['e_PH / (kJ / kg)', 'E_PH / MW'])
        for c in self.conns.index:
            row = (c.source.label + ':' + c.source_id + ' -> ' +
                   c.target.label + ':' + c.target_id)
            row_data = [c.ex_physical/10**3, c.Ex_physical/10**6]
            df.loc[row] = row_data

        self.df_exergy_conns = df

        print('\n##### RESULTS (connections) Specific physical exergy and ' +
              'physical exergy #####')
        print(tabulate(df, headers='keys', tablefmt='psql', floatfmt='.4f'))

    def print_exergy_analysis(self, E_D_min=1000, sort_desc=True):
        r"""Print the results of the exergy analysis to prompt.

        - The results are sorted beginning with the component having the
          biggest exergy destruction by default.
        - Components with an exergy destruction smaller than 1000 W is not
          printed to prompt by default.

        Parameters
        ----------
        E_D_min : float
            Minimum exergy destruction to be printed to prompt.

        sort_des : boolean
            Sort the component results descending by exergy destruction.
        """
        if sort_desc:
            df = self.component_exergy_data.sort_values(
                by=['E_D'], ascending=False)

        print('\n##### RESULTS (components) Exergy analysis #####')
        print(tabulate(
            df[df['E_D'] > E_D_min], headers='keys',
            tablefmt='psql', floatfmt='.3e', showindex=False))

        # print network exergy analysis results
        df = pd.DataFrame(
            columns=['E_P', 'E_F', 'E_L', 'E_D', 'epsilon'])
        row_data = [self.E_P, self.E_F, self.E_L, self.E_D, self.epsilon]
        df.loc['network'] = row_data
        print('\n##### RESULTS (network) Exergy analysis #####')
        print(tabulate(df, headers='keys', tablefmt='psql', floatfmt='.3e'))

# %% saving

    def save(self, path, **kwargs):
        r"""
        Save the results to results files.

        Parameters
        ----------
        filename : str
            Path for the results.

        Note
        ----
        Results will be saved to path. The results contain:

        - network.json (network information)
        - connections.csv (connection information)
        - folder components containing .csv files for busses and
          characteristics as well as .csv files for all types of components
          within your network.
        """
        if path[-1] != '/' and path[-1] != '\\':
            path += '/'
        path = hlp.modify_path_os(path)

        logging.debug('Saving network to path ' + path + '.')
        # creat path, if non existent
        if not os.path.exists(path):
            os.makedirs(path)

        # create path for component folder if non existent
        path_comps = hlp.modify_path_os(path + 'components/')
        if not os.path.exists(path_comps):
            os.makedirs(path_comps)

        # save all network information
        self.save_network(path + 'network.json')
        self.save_connections(path + 'connections.csv')
        self.save_components(path_comps)
        self.save_busses(path_comps + 'bus.csv')
        self.save_characteristics(path_comps)

    def save_network(self, fn):
        r"""
        Save basic network configuration.

        Parameters
        ----------
        fn : str
            Path/filename for the network configuration file.
        """
        data = {}
        data['m_unit'] = self.m_unit
        data['m_range'] = list(self.m_range)
        data['p_unit'] = self.p_unit
        data['p_range'] = list(self.p_range)
        data['h_unit'] = self.h_unit
        data['h_range'] = list(self.h_range)
        data['T_unit'] = self.T_unit
        data['x_unit'] = self.x_unit
        data['v_unit'] = self.v_unit
        data['s_unit'] = self.s_unit
        data['fluids'] = self.fluids_backends

        with open(fn, 'w') as f:
            f.write(json.dumps(data, indent=4))

        logging.debug('Network information saved to ' + fn + '.')

    def save_connections(self, fn):
        r"""
        Save the connection properties.

        - Uses connections object id as row identifier and saves

            - connections source and target as well as
            - properties with references and
            - fluid vector (including user specification if structure is True).

        - Connections source and target are identified by its labels.

        Parameters
        ----------
        fn : str
            Path/filename for the file.
        """
        f = Network.get_props
        df = pd.DataFrame()
        # connection id
        df['id'] = self.conns.apply(Network.get_id, axis=1)

        # general connection parameters
        # source
        df['source'] = self.conns.apply(f, axis=1, args=('source', 'label'))
        df['source_id'] = self.conns['source_id']
        # target
        df['target'] = self.conns.apply(f, axis=1, args=('target', 'label'))
        df['target_id'] = self.conns['target_id']

        # design and offdesign properties
        cols = ['design', 'offdesign', 'design_path', 'local_design',
                'local_offdesign', 'label']
        for key in cols:
            df[key] = self.conns.apply(f, axis=1, args=(key,))

        # fluid properties
        cols = ['m', 'p', 'h', 'T', 'x', 'v', 'Td_bp']
        for key in cols:
            # values and units
            df[key] = self.conns.apply(f, axis=1, args=(key, 'val'))
            df[key + '_unit'] = self.conns.apply(f, axis=1, args=(key, 'unit'))

            # connection parametrisation
            df[key + '_unit_set'] = self.conns.apply(f, axis=1,
                                                     args=(key, 'unit_set'))
            df[key + '0'] = self.conns.apply(f, axis=1, args=(key, 'val0'))
            df[key + '_set'] = self.conns.apply(f, axis=1,
                                                args=(key, 'val_set'))
            df[key + '_ref'] = self.conns.apply(f, axis=1,
                                                args=(key, 'ref', 'obj',)
                                                ).astype(str)
            df[key + '_ref'] = df[key + '_ref'].str.extract(r' at (.*?)>',
                                                            expand=False)
            df[key + '_ref_f'] = self.conns.apply(f, axis=1,
                                                  args=(key, 'ref', 'f',))
            df[key + '_ref_d'] = self.conns.apply(f, axis=1,
                                                  args=(key, 'ref', 'd',))
            df[key + '_ref_set'] = self.conns.apply(f, axis=1,
                                                    args=(key, 'ref_set',))

        # state property
        key = 'state'
        df[key] = self.conns.apply(f, axis=1, args=(key, 'val'))
        df[key + '_set'] = self.conns.apply(f, axis=1, args=(key, 'is_set'))

        # fluid composition
        for val in self.fluids:
            # fluid mass fraction
            df[val] = self.conns.apply(f, axis=1, args=('fluid', 'val', val))

            # fluid mass fraction parametrisation
            df[val + '0'] = self.conns.apply(f, axis=1,
                                             args=('fluid', 'val0', val))
            df[val + '_set'] = self.conns.apply(f, axis=1,
                                                args=('fluid', 'val_set', val))

        # fluid balance
        df['balance'] = self.conns.apply(f, axis=1, args=('fluid', 'balance'))

        df.to_csv(fn, sep=';', decimal='.', index=False, na_rep='nan')
        logging.debug('Connection information saved to ' + fn + '.')

    def save_components(self, path):
        r"""
        Save the component properties.

        - Uses components labels as row identifier.
        - Writes:

            - component's incomming and outgoing connections (object id) and
            - component's parametrisation.

        Parameters
        ----------
        path : str
            Path/filename for the file.
        """
        busses = self.busses.values()
        # create / overwrite csv file

        df_comps = self.comps.copy()

        # busses
        df_comps['busses'] = df_comps.apply(
            Network.get_busses, axis=1, args=(busses,))

        for var in ['param', 'P_ref', 'char', 'base']:
            df_comps['bus_' + var] = df_comps.apply(
                Network.get_bus_data, axis=1, args=(busses, var))

        pd.options.mode.chained_assignment = None
        f = Network.get_props
        for c in df_comps['comp_type'].unique():
            df = df_comps[df_comps['comp_type'] == c]

            # basic information
            cols = ['label', 'design', 'offdesign', 'design_path',
                    'local_design', 'local_offdesign']
            for col in cols:
                df[col] = df.apply(f, axis=1, args=(col,))

            # attributes
            for col, data in df.index[0].variables.items():
                # component characteristics container
                if isinstance(data, dc_cc) or isinstance(data, dc_cm):
                    df[col] = df.apply(
                        f, axis=1, args=(col, 'func')).astype(str)
                    df[col] = df[col].str.extract(r' at (.*?)>', expand=False)
                    df[col + '_set'] = df.apply(
                        f, axis=1, args=(col, 'is_set'))
                    df[col + '_param'] = df.apply(
                        f, axis=1, args=(col, 'param'))

                # component property container
                elif isinstance(data, dc_cp):
                    df[col] = df.apply(f, axis=1, args=(col, 'val'))
                    df[col + '_set'] = df.apply(
                        f, axis=1, args=(col, 'is_set'))
                    df[col + '_var'] = df.apply(
                        f, axis=1, args=(col, 'is_var'))

                # component property container
                elif isinstance(data, dc_simple):
                    df[col] = df.apply(f, axis=1, args=(col, 'val'))
                    df[col + '_set'] = df.apply(
                        f, axis=1, args=(col, 'is_set'))

                # component property container
                elif isinstance(data, dc_gcp):
                    df[col] = df.apply(f, axis=1, args=(col, 'method'))

            df.set_index('label', inplace=True)
            fn = path + c + '.csv'
            df.to_csv(fn, sep=';', decimal='.', index=True, na_rep='nan')
            logging.debug(
                'Component information (' + c + ') saved to ' + fn + '.')

    def save_busses(self, fn):
        r"""
        Save the bus properties.

        Parameters
        ----------
        fn : str
            Path/filename for the file.
        """
        if len(self.busses) > 0:
            df = pd.DataFrame(
                {'id': self.busses.values()}, index=self.busses.values())
            df['label'] = df.apply(Network.get_props, axis=1, args=('label',))
            df['P'] = df.apply(Network.get_props, axis=1, args=('P', 'val'))
            df['P_set'] = df.apply(Network.get_props, axis=1,
                                   args=('P', 'is_set'))
            df.drop('id', axis=1, inplace=True)

            df.set_index('label', inplace=True)
            df.to_csv(fn, sep=';', decimal='.', index=True, na_rep='nan')
            logging.debug('Bus information saved to ' + fn + '.')

    def save_characteristics(self, path):
        r"""
        Save the characteristics.

        Parameters
        ----------
        fn : str
            Path/filename for the file.
        """
        # components
        df_comps = self.comps.copy()

        # characteristic lines in components
        char_lines = []
        char_maps = []
        for c in df_comps['comp_type'].unique():
            df = df_comps[df_comps['comp_type'] == c]

            for col, data in df.index[0].variables.items():
                if isinstance(data, dc_cc):
                    char_lines += [data.func]
                elif isinstance(data, dc_cm):
                    char_maps += [data.func]

        # characteristic lines in busses
        for bus in self.busses.values():
            for c in bus.comps.index:
                ch = bus.comps.loc[c, 'char']
                if ch not in char_lines:
                    char_lines += [ch]

        # characteristic line export
        if len(char_lines) > 0:
            # get id and data
            df = pd.DataFrame({'id': char_lines}, index=char_lines)
            df['id'] = df.apply(Network.get_id, axis=1)
            df['type'] = df.apply(Network.get_class_base, axis=1)

            cols = ['x', 'y', 'extrapolate']
            for val in cols:
                df[val] = df.apply(Network.get_props, axis=1, args=(val,))

            # write to char.csv
            fn = path + 'char_line.csv'
            df.to_csv(fn, sep=';', decimal='.', index=False, na_rep='nan')
            logging.debug(
                'Characteristic line information saved to ' + fn + '.')

        if len(char_maps) > 0:
            # get id and data
            df = pd.DataFrame({'id': char_maps}, index=char_maps)
            df['id'] = df.apply(Network.get_id, axis=1)
            df['type'] = df.apply(Network.get_class_base, axis=1)

            cols = ['x', 'y', 'z1', 'z2']
            for val in cols:
                df[val] = df.apply(Network.get_props, axis=1, args=(val,))

            # write to char_map.csv
            fn = path + 'char_map.csv'
            df.to_csv(fn, sep=';', decimal='.', index=False, na_rep='nan')
            logging.debug(
                'Characteristic map information saved to ' + fn + '.')

    @staticmethod
    def get_id(c):
        """Return the id of the python object."""
        return str(c.name)[str(c.name).find(' at ') + 4:-1]

    @staticmethod
    def get_class_base(c):
        """Return the class name."""
        return c.name.__class__.__name__

    @staticmethod
    def get_props(c, *args):
        """Return properties."""
        if hasattr(c.name, args[0]):
            if (not isinstance(c.name.get_attr(args[0]), int) and
                    not isinstance(c.name.get_attr(args[0]), str) and
                    not isinstance(c.name.get_attr(args[0]), float) and
                    not isinstance(c.name.get_attr(args[0]), list) and
                    not isinstance(c.name.get_attr(args[0]), np.ndarray) and
                    not isinstance(c.name.get_attr(args[0]), con.Connection)):
                if len(args) == 1:
                    return c.name.get_attr(args[0])
                elif args[0] == 'fluid' and args[1] != 'balance':
                    return c.name.fluid.get_attr(args[1])[args[2]]
                elif args[1] == 'ref':
                    obj = c.name.get_attr(args[0]).get_attr(args[1])
                    if obj is not None:
                        return obj.get_attr(args[2])
                    else:
                        return np.nan
                else:
                    return c.name.get_attr(args[0]).get_attr(args[1])
            elif isinstance(c.name.get_attr(args[0]), np.ndarray):
                if len(c.name.get_attr(args[0]).shape) > 1:
                    return tuple(c.name.get_attr(args[0]).tolist())
                else:
                    return c.name.get_attr(args[0]).tolist()
            else:
                return c.name.get_attr(args[0])

    @staticmethod
    def get_busses(c, *args):
        """Return the list of busses a component is integrated in."""
        busses = []
        for bus in args[0]:
            if c.name in bus.comps.index:
                busses += [bus.label]
        return busses

    @staticmethod
    def get_bus_data(c, *args):
        """Return bus information of a component."""
        items = []
        if args[1] == 'char':
            for bus in args[0]:
                if c.name in bus.comps.index:
                    val = bus.comps.loc[c.name, args[1]]
                    items += [str(val)[str(val).find(' at ') + 4:-1]]

        else:
            for bus in args[0]:
                if c.name in bus.comps.index:
                    items += [bus.comps.loc[c.name, args[1]]]

        return items
