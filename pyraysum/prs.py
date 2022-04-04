# Copyright 2020 Pascal Audet

# This file is part of PyRaysum.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

'''

Functions to interact with ``Raysum`` software

'''
import subprocess
import types
from datetime import datetime
import numpy as np
from scipy import signal
import pandas as pd
import matplotlib.pyplot as plt
from obspy import Trace, Stream, UTCDateTime
from obspy.core import AttribDict
from numpy.fft import fft, ifft, fftshift
from pyraysum import wiggle
import fraysum


class Model(object):
    """
    Model of the subsurface seismic velocity structure 
    
    ``Parameters``:
        - thickn (np.ndarray): 
            Thickness of layers (m) (shape ``(nlay)``)
        - rho (np.ndarray): 
            Density (kg/m^3) (shape ``(nlay)``)
        - vp (np.ndarray): 
            P-wave velocity (m/s) (shape ``(nlay)``)
        - vs (np.ndarray): 
            S-wave velocity (m/s) (shape ``(nlay)``)
        - vpvs (np.ndarray): 
            P-to-S velocity ratio (shape ``(nlay)``)
            Ignored unless ``update`` with keyword 'fix' is used.
        - flag (list of str, optional, defaut: ``1`` or isotropic):
            Flags for type of layer material (dimension ``nlay``)
        - ani (np.ndarray, optional): 
            Anisotropy (percent) (shape ``(nlay)``)
        - trend (np.ndarray, optional):
            Trend of symmetry axis (degree) (shape ``(nlay)``)
        - plunge (np.ndarray, optional):
            Plunge of symmetry axis (degree) (shape ``(nlay)``)
        - strike (np.ndarray, optional):
            azimuth of interface in RHR (degree) (shape ``(nlay)``)
        - dip (np.ndarray, optional):
            dip of interface in RHR (degree) (shape ``(nlay)``)
        - nlay (int): 
            Number of layers

        To broadcast the model to the the fortran routine use

        - maxlay (int): 
            Maximum number of layers defined in params.h
        - fthickn (np.ndarray): 
            Thickness of layers (m) (shape ``(maxlay)``)
        - frho (np.ndarray): 
            Density (kg/m^3) (shape ``(maxlay)``)
        - fvp (np.ndarray): 
            P-wave velocity (m/s) (shape ``(maxlay)``)
        - fvs (np.ndarray): 
            S-wave velocity (m/s) (shape ``(maxlay)``)
        - fflag (list of str, optional, defaut: ``1`` or isotropic):
            Flags for type of layer material (dimension ``maxlay``)
        - fani (np.ndarray, optional): 
            Anisotropy (percent) (shape ``(maxlay)``)
        - ftrend (np.ndarray, optional):
            Trend of symmetry axis (radians) (shape ``(maxlay)``)
        - fplunge (np.ndarray, optional):
            Plunge of symmetry axis (radians) (shape ``(maxlay)``)
        - fstrike (np.ndarray, optional):
            azimuth of interface in RHR (radians) (shape ``(maxlay)``)
        - fdip (np.ndarray, optional):
            dip of interface in RHR (radians) (shape ``(maxlay)``)

        .. note:: 

            To optimize construction of models, build the input arrays for
            ``pyraysum.run_frs()`` in the correct shape.

            TODO: ``- a (np.ndarray): Elastic thickness (shape ``(3, 3, 3, 3, nlay)``)``
    """

    def __init__(self, thickn, rho, vp, vs, flag=1,
                 ani=None, trend=None, plunge=None,
                 strike=None, dip=None, maxlay=15):

        def _get_val(v):
            if v is not None:
                return np.array([v] * self.nlay
                                if isinstance(v, (int, float))
                                else v)
            else:
                return np.array([0.]*self.nlay)

        self.nlay = len(thickn)
        self.thickn = np.array(thickn)
        self.rho = np.array(rho) if rho is not None else [None] * self.nlay
        self.vp = np.array(vp)
        self.vs = np.array(vs)
        self.vpvs = self.vp / self.vs
        self.flag = np.array([flag] * self.nlay if isinstance(flag, int)
                             else list(flag))
        self.ani = _get_val(ani)
        self.trend = _get_val(trend)
        self.plunge = _get_val(plunge)
        self.strike = _get_val(strike)
        self.dip = _get_val(dip)

        self.maxlay = maxlay

        self._set_fattributes()

        self._useratts = ['thickn', 'rho', 'vp', 'vs', 'vpvs', 'flag', 'ani',
                          'trend', 'plunge', 'strike', 'dip']


    def __len__(self):
        return self.nlay

    def __str__(self):
        buf = '# thickn     rho      vp      vs  flag aniso  trend  '
        buf += 'plunge  strike   dip\n'

        f = '{: 8.1f} {: 7.1f} {: 7.1f} {: 7.1f}    {: 1.0f} {: 5.1f} {: 6.1f}   '
        f += '{: 5.1f}  {: 6.1f} {: 5.1f}\n'

        for th, vp, vs, r, fl, a, tr, p, s, d in zip(
                self.thickn, self.vp, self.vs, self.rho, self.flag, self.ani,
                self.trend, self.plunge, self.strike, self.dip):
            buf += f.format(th, r, vp, vs, fl, a, tr, p, s, d)

        return buf

    def _set_fattributes(self):
        tail = np.zeros(self.maxlay - self.nlay)
        self.fthickn = np.asfortranarray(np.append(self.thickn, tail))
        self.frho = np.asfortranarray(np.append(self.rho, tail))
        self.fvp = np.asfortranarray(np.append(self.vp, tail))
        self.fvs = np.asfortranarray(np.append(self.vs, tail))
        self.fflag = np.asfortranarray(np.append(self.flag, tail))
        self.fani = np.asfortranarray(np.append(self.ani, tail))
        self.ftrend = np.asfortranarray(np.append(self.trend, tail) * np.pi/180)
        self.fplunge = np.asfortranarray(np.append(self.plunge, tail) * np.pi/180)
        self.fstrike = np.asfortranarray(np.append(self.strike, tail) * np.pi/180)
        self.fdip = np.asfortranarray(np.append(self.dip, tail) * np.pi/180)

    def update(self, fix=None):
        """
        Update fortran attributes after user attributes have changed.

        Args:
            fix : None or (str)
                Change vp or vs according to vpvs attribute, where vpvs = vp/vs
                None: Ignore vpvs attribute
                'vp': Keep vp fixed and set vs = vp / vpvs
                'vs': Keep vs fixed and set vp = vs * vpvs
        """

        if fix == 'vp':
            self.vs = self.vp / self.vpvs
        elif fix == 'vs':
            self.vp = self.vs * self.vpvs
        elif not fix:
            self.vpvs = self.vp / self.vs
        else:
            msg = 'Unknown fix keyword: ' + fix
            raise ValueError(msg)

        self._set_fattributes()

    def change(self, commands):
        """
        Change model layers using simple command sting.

        Args:

        commands (str)
        An arbitray number command substrings seperated by ';'. Each substring
        has the form:

        KEY LAYER SIGN VAL;
        
        where

        KEY [t|vp|vs|psp|pss|s|d|a|tr|pl] is the attribute to change
            t    thicknes (km)
            vp   P wave velocity (km/s)
            vs   S wave velocity (km/s)
            psp  P to S wave velocity ratio with fixed S wave velocity
                 (changing P wave velocity)
            pss  P to S wave velocity ratio with fixed P wave velocity
                 (changing S wave velocity)
            s    strike (deg)
            d    dip (deg)
            a    anisotropy %
            tr   trend of the anisotropy axis (deg)
            pl   plunge ot the anisotropy axis (deg)
        
        LAYER (int) is the index of the layer
        
        SIGN [=|+|-] is to set / increase / decrease the attribute
        
        VAL (float) is the value to which to change or by which to increase
                    or decrease

        Example:

        Model.change('t0+10;psp0-0.2;d1+5;s1=45') does
        1. Increase the thickness of the first layer by 10 km
        2. Decrease Vp/Vs of the of the first layer by 0.2, holding Vs fixed
        3. Increase the dip of the second layer by 5 degree
        4. Set the strike of the second layer to 45 degree
        """
        ATT = {'t': 'thickn',
               'vp': 'vp',
               'vs': 'vs',
               'psp': 'vpvs',
               'pss': 'vpvs',
               's': 'strike',
               'd': 'dip',
               'a': 'ani',
               'tr': 'trend',
               'pl': 'plunge'}

        for command in commands.split(';'):
            if not command:
                continue

            # split by sign
            for sign in '=+-':
                ans = command.split(sign)
                if len(ans) == 2:
                    break

            (attlay, inc) = ans

            # Split attribute and layer
            for n, char in enumerate(attlay):
                if char in '0123456789':
                    break

            att = attlay[:n].strip()
            lay = int(attlay[n:])
            inc = float(inc)

            # convert thicknes and velocities from kilometers
            if att in ['t', 'vp', 'vs']:
                inc *= 1000

            # Which velocity to fix
            fix = None
            if att == 'pss':
                fix = 'vp'
            if att == 'psp':
                fix = 'vs'

            attribute = ATT[att]

            # Apply
            if sign == '=':
                self.__dict__[attribute][lay] = inc
                sign = '' # to print nicely below
            elif sign == '+':
                self.__dict__[attribute][lay] += inc
            elif sign == '-':
                self.__dict__[attribute][lay] -= inc

            # Set isotropy flag iff layer is isotropic
            self.flag[lay] = 1
            if self.ani[lay] != 0:
                self.flag[lay] = 0

            self.update(fix=fix)

            msg = 'Changed: {:}[{:d}] {:}= {:}'.format(attribute, lay, sign, inc)
            print(msg)

    def split_layer(self, n):
        """
        Split layer n into two with half the thickness each, but otherwise
        identical parameters.

        Args:
            n : (int)
                Index of the layer to split
        """

        for att in self._useratts:
            self.__dict__[att] = np.insert(self.__dict__[att], n,
                                           self.__dict__[att][n])

        self.thickn[n] /= 2
        self.thickn[n+1] /= 2
        self.nlay += 1

        self.update()

    def remove_layer(self, n):
        """
        Remove layer n

        Args:
            n : (int)
                Index of the layer to remove
        """

        for att in self._useratts:
            self.__dict__[att] = np.delete(self.__dict__[att], n)

        self.nlay -= 1
        self.update()

    def combine_layers(self, top, bottom):
        """
        Combine layers between top and bottom index into one with summed
        thicknes and average vp, vs, and rho.

        Args:
            top : (int)
                Index of topmost layer to include in combination
            bottom : (int)
                Index of bottommost layer to include in combination

        Raises:
            IndexError if bottom less or equal top
            ValueError if any layer is anisotropic
            ValueError if any layer has a differs in strike or dip
        """
        if bottom <= top:
            raise IndexError('bottom must be larger than top.')

        bot = bottom + 1

        if not all(self.flag[top:bot]):
            raise ValueError('Can only combine isotropic layers')

        if not all(self.dip[top:bot][0] == self.dip[top:bot]):
            raise ValueError('All layers must have the same dip')

        if not all(self.strike[top:bot][0] == self.strike[top:bot]):
            raise ValueError('All layers must have the same strike')

        thickn = sum(self.thickn[top:bot])
        weights = self.thickn[top:bot] / thickn

        layer = {'thickn': thickn,
                 'vp': sum(self.vp[top:bot] * weights),
                 'vs': sum(self.vs[top:bot] * weights),
                 'rho': sum(self.rho[top:bot] * weights)}

        for att in self._useratts:
            try:
                self.__dict__[att][top] = layer[att]
            except KeyError:
                pass
            self.__dict__[att] = np.delete(self.__dict__[att],
                                           range(top+1, bot))

        self.nlay -= bottom - top
        self.update()

    def times(self):
        """
        Arrival times (seconds) of the direct conversions for vertically
        propagating tays.
        """
        # TODO: Supply Geometry object to calculate for a given ray geometry
        return np.cumsum(self.thickn/self.vs - self.thickn/self.vp)[:-1]

    def save(self, fname='sample.mod', comment=''):
        """
        Save seismic velocity model to raysum model file

        Args:
            fname (str): Name of the output file
            comment (str): String to write into file header

        """

        if not comment.startswith('#'):
            comment = '# ' + comment
        if not comment.endswith('\n'):
            comment += '\n'

        if not isinstance(fname, str):
            print("Warning: filename reverts to default 'sample.mod'")
            fname = 'sample.mod'

        buf = '# Raysum velocity model created with PyRaysum\n'
        buf += '# on: {:}\n'.format(datetime.now().isoformat(' ', 'seconds'))
        buf += comment
        buf += self.__str__()

        with open(fname, 'w') as fil:
            fil.write(buf)

    def plot(self, zmax=75.):
        """
        Plot model as both stair case and layers - show it

        Args:
            zmax (float): Maximum depth of model to plot (km)

        """

        # Initialize new figure
        fig = plt.figure(figsize=(10, 5))

        # Add subplot for profile
        ax1 = fig.add_subplot(1, 4, 1)
        self.plot_profile(zmax=zmax, ax=ax1)

        # Add subplot for layers
        ax2 = fig.add_subplot(1, 4, 2)
        self.plot_layers(zmax=zmax, ax=ax2)

        ax3 = fig.add_subplot(1, 4, (3, 4))
        self.plot_interfaces(zmax=zmax, ax=ax3)

        # Tighten the plot and show it
        plt.tight_layout()
        plt.show()


    def plot_profile(self, zmax=75., ax=None):
        """
        Plot model as stair case and show it

        Args:
            zmax (float): 
                Maximum depth of model to plot (km)
            ax (plt.axis): 
                Axis handle for plotting. If ``None``, show the plot

        Returns:
            (plt.axis): 
                ax: Axis handle for plotting. 
        """

        # Defaults to not show the plot
        show = False

        # Find depths of all interfaces in km
        thickn = self.thickn.copy()
        if thickn[-1] == 0.:
            thickn[-1] = 50000.
        depths = np.concatenate(([0.], np.cumsum(thickn)))/1000.

        # Get corner coordinates of staircase representation of model
        depth = np.array(list(zip(depths[:-1], depths[1:]))).flatten()
        vs = np.array(list(zip(self.vs, self.vs))).flatten()
        vp = np.array(list(zip(self.vp, self.vp))).flatten()
        rho = np.array(list(zip(self.rho, self.rho))).flatten()
        ani = np.array(list(zip(self.ani, self.ani))).flatten()

        # Generate new plot if an Axis is not passed
        if ax is None:
            fig = plt.figure(figsize=(5,5))
            ax = fig.add_subplot(111)
            show = True

        # Plot background model
        ax.plot(vs, depth, color="C0", label=r'Vs (m s$^{-1}$)')
        ax.plot(vp, depth, color="C1", label=r'Vp (m s$^{-1}$)')
        ax.plot(rho, depth, color="C2", label=r'Density (kg m$^{-3}$)')

        # If there is anisotropy, show variability
        if np.any([flag == 0 for flag in self.flag]):
            ax.plot(vs*(1. - ani/100.), depth, '--', color="C0")
            ax.plot(vs*(1. + ani/100.), depth, '--', color="C0")
            ax.plot(vp*(1. - ani/100.), depth, '--', color="C1")
            ax.plot(vp*(1. + ani/100.), depth, '--', color="C1")

        # Fix axes and add labels
        ax.legend(fontsize=8)
        ax.set_xlabel('Velocity or Density')
        ax.set_ylabel('Depth (km)')
        ax.set_ylim(0., zmax)
        ax.invert_yaxis()
        ax.grid(ls=':')

        if show:
            plt.show()

        return ax


    def plot_layers(self, zmax=75., ax=None):
        """
        Plot model as horizontal layers and show it

        Args:
            zmax (float): 
                Maximum depth of model to plot (km)
            ax (plt.axis): 
                Axis handle for plotting. If ``None``, show the plot

        Returns:
            (plt.axis): 
                ax: Axis handle for plotting

        .. note:: 

            Change current routine for painting approach
            - [ ] paint background with top layer
            - [ ] paint layer 1 from top to bottom (incl. dip layer)
            - [ ] continue until bottom of model
        """

        # Defaults to not show the plot
        show = False

        # Find depths of all interfaces
        thickn = self.thickn.copy()
        if thickn[-1] == 0.:
            thickn[-1] = 50000.
        depths = np.concatenate(([0.], np.cumsum(thickn)))/1000.

        # Generate new plot if an Axis is not passed
        if ax is None:
            fig = plt.figure(figsize=(2,5))
            ax = fig.add_subplot(111)
            show = True

        # Define color palette
        norm = plt.Normalize()
        colors = plt.cm.GnBu(norm(self.vs))

        # Cycle through layers
        for i in range(len(depths) - 1):

            # If anisotropic, add texture - still broken hatch
            if not self.flag[i] == 1:
                cax = ax.axhspan(depths[i], depths[i+1],
                    color=colors[i])
                cax.set_hatch('o')
            # Else isotropic
            else:
                cax = ax.axhspan(depths[i], depths[i+1],
                    color=colors[i])

        # Fix axes and labelts
        ax.set_ylim(0., zmax)
        ax.set_xticks(())
        ax.invert_yaxis()

        if show:
            ax.set_ylabel('Depth (km)')
            plt.tight_layout()
            plt.show()

        return ax


    def plot_interfaces(self, zmax=75, ax=None):
        """
        Plot model as interfaces with possibly dipping layers

        Args:
            zmax (float): 
                Maximum depth of model to plot (km)
            ax (plt.axis): 
                Axis handle for plotting. If ``None``, show the plot

        Returns:
            (plt.axis): ax: Axis handle for plotting

        """

        # Defaults to not show the plot
        show = False

        # Find depths of all interfaces
        depths = np.concatenate(([0.], np.cumsum(self.thickn)))/1000.
        maxdep = depths[-1] + 50
        xs = np.array([-maxdep/2, maxdep/2])

        # Generate new plot if an Axis is not passed
        if ax is None:
            fig = plt.figure(figsize=(4, 4))
            ax = fig.add_subplot(111)
            show = True

        ax.scatter(0, -0.6, 60, marker='v', c='black')
        # Cycle through layers
        for i, depth in enumerate(depths[:-1]):
            dzdx = np.sin(self.dip[i]*np.pi/180)
            zs = depth + xs*dzdx
            ax.plot(xs, zs, color='black')
            dipdir = (self.strike[i] + 90) % 360

            if i == 0 or self.strike[i] != self.strike[i-1]:
                ax.text(xs[-1], zs[-1], '>{:.0f}°'.format(dipdir),
                        ha='left', va='center')

            info = ('$V_P = {:.1f}$km/s, $V_S = {:.1f}$km/s, '
                    '$\\rho = {:.1f}$kg/m$^3$').format(
                    self.vp[i]/1000, self.vs[i]/1000, self.rho[i]/1000)
            ax.text(0, depth, info,
                    rotation=-self.dip[i],
                    rotation_mode='anchor',
                    ha='center', va='top')


            if self.flag[i] == 0:
                aninfo = '--{:.0f}%--{:.0f}°'.format(
                          self.ani[i], self.trend[i])
                ax.text(xs[-1], zs[-1] + self.thickn[i]/2000, aninfo,
                        rotation=-self.plunge[i],
                        rotation_mode='anchor',
                        ha='center', va='center')

        # Fix axes and labels
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.xaxis.set_ticks_position('bottom')
        ax.yaxis.set_ticks_position('left')
        ax.set_ylim(0., zmax)
        ax.axis('equal')
        ax.invert_yaxis()

        if show:
            ax.set_ylabel('Depth (km)')
            plt.tight_layout()
            plt.show()

        return ax


def read_model(modfile, encoding=None):
    """
    Reads model parameters from file and returns an instance of class 
    :class:`~pyraysum.prs.Model`.

    Returns:
        (:class:`~pyraysum.prs.Model`): model: Seismic velocity model for current simulation

    """
    values = np.genfromtxt(modfile, dtype=None, encoding=encoding)
    return Model(*zip(*values))


class Geometry(object):
    """
    Recording geometry at the seismic station. Compute one synthetic trace for
    each array element.

    ``Parameters``:
        - baz (np.ndarray): 
            Ray backazimuths (deg)
        - slow (np.ndarray): 
            Ray slownesses (km/s)
        - geom (np.ndarray): 
            Array of zipped [baz, slow] pairs.
        - dx (np.ndarray): 
            North-offset of the seismic station (m) (shape ``(ntr)``)
        - dy (np.ndarray): 
            East-offset of the seismic station (m) (shape ``(ntr)``)
        - ntr (int): 
            Number of traces

        To broadcast the model to the the fortran routine use:

        - maxtr (int): 
            Maximum number of traces defined in params.h
        - fbaz (np.ndarray): 
            Ray backazimuth (radians) (shape ``(maxtr)``)
        - fslow (np.ndarray): 
            Ray slowness (m/s) (shape ``(maxtr)``)
        - fdx (np.ndarray): 
            North-offset of the seismic station (m) (shape ``(maxtr)``)
        - fdy (np.ndarray): 
            East-offset of the seismic station (m) (shape ``(maxtr)``)

    .. note::

        To optimize construction of ray geometries, build the input
        arrays for ``pyraysum.run_frs()`` in the correct shape.
    """


    def __init__(self, baz, slow, dx=[0], dy=[0], maxtr=500):

        if type(baz) == int or type(baz) == float:
            baz = [baz]

        if type(slow) == int or type(slow) == float:
            slow = [slow]

        if len(baz) != len(slow):
            self.geom = [(bb, ss) for ss in slow for bb in baz]
        else:
            self.geom = [(bb, ss) for bb, ss in zip(baz, slow)]

        baz, slow = zip(*self.geom)
        self.baz = np.array(baz)
        self.slow = np.array(slow)

        self.ntr = len(self.baz)

        self.dx = np.array(dx)
        self.dy = np.array(dy)

        if len(self.dx) != self.ntr:
            self.dx = np.full(self.ntr, self.dx[0])

        if len(self.dy) != self.ntr:
            self.dy = np.full(self.ntr, self.dy[0])

        tail = np.zeros(maxtr - self.ntr)
        self.fbaz = np.asfortranarray(np.append(self.baz, tail) * np.pi/180)
        self.fslow = np.asfortranarray(np.append(self.slow, tail) * 1e-3)
        self.fdx = np.asfortranarray(np.append(self.dx, tail))
        self.fdy = np.asfortranarray(np.append(self.dy, tail))

    def __len__(self):
        return self.ntr

    def __str__(self):
        out = ''
        form = '{: 7.2f} {: 8.4f} {:7.2f} {:7.2f}\n'
        for bb, ss, xx, yy in zip(self.baz, self.slow, self.dx, self.dy):
            out += form.format(bb, ss, xx, yy)
        return out

    def save(self, fname='sample.geom'):
        """
        Save ray geometry as ascii file

        Args:
            fname: (str)
            Name of file
        """

        with open(fname, "w") as f:
            f.write(self.__str__())

        print('Geometry saved to: ' + fname)

def read_geometry(geomfile, encoding=None):
    """
    Reads geometry parameters from file and returns an instance of class 
    :class:`~pyraysum.prs.Geometry`.

    Returns:
        (:class:`~pyraysum.prs.Geometry`):
        geometry: Ray geometry for current simulation

    """
    values = np.genfromtxt(geomfile, dtype=None, encoding=encoding)
    return Geometry(*zip(*values))

class StreamList(object):
    """
    List of streams of 3-component synthetic seismograms produced by Raysum. 
    Includes methods to calculate receiver functions, filter and plot the 
    streams.

    ``Parameters``:
        - model (:class:`~pyraysum.prs.Model`): 
            Instance of class :class:`~pyraysum.prs.Model`
        - geom (:class:`~pyraysum.prs.Geometry`): 
            Instance of class :class:`~pyraysum.prs.Geometry`
        - streams (List): 
            List of :class:`~obspy.core.Stream` objects.
        - args (Dictionary): 
            Dictionary attributes of all input arguments

    """


    def __init__(self, model=None, geom=None, streams=None,
                 args=None):

        self.model = model
        self.geom = geom
        self.streams = streams
        self.args = AttribDict(args)


    def calculate_rfs(self):
        """
        Method to generate receiver functions from displacement traces. Also
        stores ``rflist`` as attribute of the :class:`~pyraysum.prs.StreamList`
        object.

        Returns:
            (list): 
                rflist: Stream containing Radial and Transverse receiver functions

        """

        if self.args.rot == 0:
            msg = "Receiver functions cannot be calculated with 'rot == 0'\n"
            raise(Exception(msg))

        if self.args.rot == 1:
            cmpts = ['R', 'T', 'Z']
        elif self.args.rot == 2:
            cmpts = ['V', 'H', 'P']
        else:
            raise(Exception('rotation ID invalid: '+str(self.args.rot)))

        rflist = []

        # Cycle through list of displacement streams
        for stream in self.streams:

            # Calculate time axis
            npts = stream[0].stats.npts
            taxis = np.arange(-npts/2., npts/2.)*stream[0].stats.delta

            # Extract 3-component traces from stream
            rtr = stream.select(component=cmpts[0])[0]
            ttr = stream.select(component=cmpts[1])[0]
            ztr = stream.select(component=cmpts[2])[0]

            # Deep copy and re-initialize data to 0.
            rfr = rtr.copy()
            rfr.data = np.zeros(len(rfr.data))
            rft = ttr.copy()
            rft.data = np.zeros(len(rft.data))

            # Fourier transform
            ft_rfr = fft(rtr.data)
            ft_rft = fft(ttr.data)
            ft_ztr = fft(ztr.data)

            # Spectral division to calculate receiver functions
            if self.args.wvtype == 'P':
                rfr.data = fftshift(np.real(ifft(np.divide(ft_rfr, ft_ztr))))
                rft.data = fftshift(np.real(ifft(np.divide(ft_rft, ft_ztr))))
            elif self.args.wvtype == 'SV':
                rfr.data = fftshift(np.real(ifft(np.divide(-ft_ztr, ft_rfr))))
            elif self.args.wvtype == 'SH':
                rft.data = fftshift(np.real(ifft(np.divide(-ft_ztr, ft_rft))))
            else:
                raise(Exception("wave typye invalid: "+self.args.wvtype))

            # Update stats
            rfr.stats.channel = 'RF'+cmpts[0]
            rft.stats.channel = 'RF'+cmpts[1]
            rfr.stats.taxis = taxis
            rft.stats.taxis = taxis

            # Store in Stream
            rfstream = Stream(traces=[rfr, rft])

            # Append to list
            rflist.append(rfstream)

        self.rfs = rflist

        return rflist


    def plot(self, typ, **kwargs):
        """ 

        Plots the displacement seismograms and/or receiver functions stored in
        :class:`~pyraysum.prs.StreamList` streams.

        Args:
            typ (str): 
                Type of plot to show. Options are ``'streams'``, 
                ``'rfs'``, or ``'all'`` for the displacement seismograms, 
                receiver functions, or both

        """
        if typ == 'streams':
            self.plot_streams(**kwargs)
        elif typ == 'rfs':
            try:
                self.plot_rfs(**kwargs)
            except:
                raise(Exception("Cannot plot 'rfs'"))
        elif typ == 'all':
            try:
                self.plot_streams(**kwargs)
                try:
                    self.plot_rfs(**kwargs)
                except:
                    raise(Exception("Cannot plot 'rfs'"))
            except:
                raise(Exception("Cannot plot 'all'"))
        else:
            msg = "'typ' has to be either 'streams', 'rfs' or 'all'"
            raise(TypeError(msg))


    def filter(self, typ, ftype, **kwargs):
        """ 

        Filters the displacement seismograms and/or receiver functions stored in
        :class:`~pyraysum.prs.StreamList` streams.

        Args:
            typ (str): 
                Type of plot to show. Options are ``'streams'``, 
                ``'rfs'``, or ``'all'`` for the displacement seismograms, 
                receiver functions, or both
            ftype (str):
                Type of filter to use. 

        """
        if typ == 'streams':
            self.filter_streams(ftype, **kwargs)
        elif typ == 'rfs':
            try:
                self.filter_rfs(ftype, **kwargs)
            except:
                raise(Exception("Cannot filter 'rfs'"))
        elif typ == 'all':
            self.filter_streams(ftype, **kwargs)
            try:
                self.filter_rfs(ftype, **kwargs)
            except:
                print("Cannot filter 'rfs'")
        else:
            msg = "'typ' has to be either 'streams', 'rfs' or 'all'"
            raise(TypeError(msg))

    def plot_streams(self, scale=1.e3, tmin=-5., tmax=20.):
        wiggle.stream_wiggles(self.streams, scale=scale, tmin=tmin, tmax=tmax)

    def plot_rfs(self, scale=1.e3, tmin=-5., tmax=20.):
        wiggle.rf_wiggles(self.rfs, scale=scale, tmin=tmin, tmax=tmax)

    def filter_streams(self, ftype, **kwargs):
        [stream.filter(ftype, **kwargs) for stream in self.streams]

    def filter_rfs(self, ftype, **kwargs):
        [rf.filter(ftype, **kwargs) for rf in self.rfs]
            


def read_traces(traces, **kwargs):
    """
    Extracts the traces produced by Raysum and stores them into a list
    of Stream objects

    Args:
        traces (np.array):
            Array holding the traces
        geom (array):
            Array of [baz, slow] values
        dt (float):
            Sample distance in seconds
        rot (int):
            ID for rotation: 0 is NEZ, 1 is RTZ, 2 is PVH
        shift (float):
            Time shift in seconds

    .. note::
        To interpret fraysum output, supply:``
            ntr (int):
                Number of traces
            npts (int):
                Number of points per trace``

    Returns:
        (:class:`~pyraysum.prs.StreamList`): streamlist: List of Stream objects

    """


    def _make_stats(net=None, sta=None, stime=None, dt=None,
                    slow=None, baz=None, wvtype=None, channel=None,
                    taxis=None):
        """
        Updates the ``stats`` dictionary from an obspy ``Trace`` object.

        Args:
            net (str): Network name
            sta (str): Station name
            stime (:class:`~obspy.core.UTCDateTime`): Start time of trace
            dt (float): Sampling distance in seconds
            slow (float): Slowness value (s/km)
            baz (float): Back-azimuth value (degrees)
            wvtype (str): Wave type ('P', 'SV', or 'SH')
            channel (str): Channel name
            taxis (:class:`~numpy.ndarray`): Time axis in seconds

        Returns:
            (:class:`~obspy.core.Trace`):
                tr: Trace with updated stats

        """

        stats = AttribDict()
        stats.baz = baz
        stats.slow = slow
        stats.station = sta
        stats.network = net
        stats.starttime = stime
        stats.delta = dt
        stats.channel = channel
        stats.wvtype = wvtype
        stats.taxis = taxis

        return stats


    # Unpack the arguments
    args = AttribDict({**kwargs})

    kwlist = ['traces', 'dt', 'geom', 'rot', 'shift', 'npts', 'ntr']

    for k in args:
        if k not in kwlist:
            raise(Exception('Incorrect kwarg: ', k))

    # Read fortran output
    npts = args.npts
    ntr = args.ntr

    # Crop unused overhang of oversized fortran arrays
    data = {'trace1': traces[0, :npts, :ntr].reshape(npts*ntr, order='F'),
            'trace2': traces[1, :npts, :ntr].reshape(npts*ntr, order='F'),
            'trace3': traces[2, :npts, :ntr].reshape(npts*ntr, order='F'),
            'itr': np.array([npts*[tr] for tr in range(ntr)]).reshape(npts*ntr)}
    df = pd.DataFrame(data=data)


    # Component names
    if args.rot == 0:
        component = ['N', 'E', 'Z']
    elif args.rot == 1:
        component = ['R', 'T', 'Z']
    elif args.rot == 2:
        component = ['P', 'V', 'H']
    else:
        raise(Exception('invalid "rot" value: not in 0, 1, 2'))

    # Number of "event" traces produced
    ntr = np.max(df.itr) + 1

    # Time axis
    npts = len(df[df.itr == 0].trace1.values)
    taxis = np.arange(npts)*args.dt - args.shift

    streams = []

    for itr in range(ntr):

        # Split by trace ID
        ddf = df[df.itr == itr]

        # Store into trace by channel with stats information
        # Channel 1

        stats = _make_stats(net='', sta='synt', stime=UTCDateTime(),
                            dt=args.dt, slow=args.geom[itr][1],
                            baz=args.geom[itr][0],
                            channel='BH'+component[0], taxis=taxis)
        tr1 = Trace(data=ddf.trace1.values, header=stats)

        # Channel 2
        stats = _make_stats(net='', sta='synt', stime=UTCDateTime(),
                            dt=args.dt, slow=args.geom[itr][1],
                            baz=args.geom[itr][0],
                            channel='BH'+component[1], taxis=taxis)
        tr2 = Trace(data=ddf.trace2.values, header=stats)

        # Channel 3
        stats = _make_stats(net='', sta='synt', stime=UTCDateTime(),
                            dt=args.dt, slow=args.geom[itr][1],
                            baz=args.geom[itr][0],
                            channel='BH'+component[2], taxis=taxis)
        tr3 = Trace(data=ddf.trace3.values, header=stats)

        # Store into Stream object and append to list
        stream = Stream(traces=[tr1, tr2, tr3])
        streams.append(stream)

    return streams


cached_coefficients = {}


def _get_cached_bandpass_coefs(order, corners):
    # from pyrocko.Trace.filter
    ck = (order, tuple(corners))
    if ck not in cached_coefficients:
        cached_coefficients[ck] = signal.butter(
            order, corners, btype='band')

    return cached_coefficients[ck]


def filtered_rf_array(sspread_arr, arr_out, ntr, npts, dt, fmin, fmax):
    """
    Reads the traces produced by seis_spread and returns array of filtered
    receiver functions. Roughly equivalent to subsequent calls to
    ``read_traces()``, ``StreamList.calculate_rfs()``, and ``StreamList.filter()``,
    stripped down for inversion purposes.

    - Reshapes them to [traces[components[amplitudes]] order
    - Performs spectral division to get receiver functions
    - Filters them

    Args:
        sspread_arr (np.array):
            Output of call_seis_spread
        arr_out (np.array):
            Initialized array of shape (ntr, 2, npts) to store output
        ntr (int):
            Number of traces (seis_spread parameter)
        npts (int):
            Number of points per trace (seis_spread parameter)
        dt (float):
            Sampling intervall (seis_spread parameter)
        fmin (float):
            Lower filter corner
        fmax (float):
            Upper filter corner

    Returns:
        None:
            Output is written to arrout (np.ndarray)

    """

    order = 2
    def _bandpass(arr):
        # from pyrocko.Trace.filter
        (b, a) = _get_cached_bandpass_coefs(order, (2*dt*fmin, 2*dt*fmax))
        arr -= np.mean(arr)
        firstpass = signal.lfilter(b, a, arr)
        return signal.lfilter(b, a, firstpass[::-1])[::-1]

    # Crop unused overhang of oversized fortran arrays and transpose to
    # [traces[components[samples]]] order
    data = np.array([sspread_arr[0, :npts, :ntr],
                     sspread_arr[1, :npts, :ntr],
                     sspread_arr[2, :npts, :ntr]]).transpose(2, 0, 1)

    for n, trace in enumerate(data):
        ft_ztr = fft(trace[0])  # P or R or N
        ft_rfr = fft(trace[1])  # V or T or E
        ft_rft = fft(trace[2])  # H or Z or Z

        # assuming PVH:
        arr_out[n, 0, :] = _bandpass(
            fftshift(np.real(ifft(np.divide(ft_rfr, ft_ztr)))))
        arr_out[n, 1, :] = _bandpass(
            fftshift(np.real(ifft(np.divide(ft_rft, ft_ztr)))))


def run_frs(model, geometry, verbose=False, wvtype='P', mults=2,
            npts=300, dt=0.025, align=1, shift=None, rot=0, rf=False):
    """
    Run Fortran Raysum

    Calls the compiled call-seis-spread binary and stores traces in a list of
    Stream objects

    Args:
        model (:class:`~pyraysum.prs.Model`):
            Subsurface velocity structure model
        model (:class:`~pyraysum.prs.Geometry`):
            Recording geometry
        verbose (bool):
            Whether or not to increase verbosity of Raysum
        wvtype (str):
            Wave type of incoming wavefield ('P', 'SV', or 'SH')
        mults (int):
            ID for calculating free surface multiples
            ('0': no multiples, '1': Moho only, '2': all first-order)
        npts (int):
            Number of samples in time series
        dt (float):
            Sampling distance in seconds
        align (int):
            ID for alignment of seismograms ('1': align at 'P',
            '2': align at 'SV' or 'SH')
        shift (float):
            Time shift in seconds (positive shift moves seismograms
            to greater lags)
        rot (int):
            ID for rotation: 0 is NEZ, 1 is RTZ, 2 is PVH
        rf (bool):
            Whether or not to calculate RFs

    Returns:
        (:class:`~pyraysum.prs.StreamList`): streamlist: List of Stream objects

    Example
    -------
    >>> from pyraysum import prs, Model, Geometry
    >>> # Define two-layer model with isotropic crust over isotropic half-space
    >>> model = Model([30000., 0], [2800., 3300.], [6000., 8000.], [3600., 4500.])
    >>> geom = Geometry(0., 0.06) # baz = 0 deg; slow = 0.06 x/km
    >>> npts = 1500
    >>> dt = 0.025      # s
    >>> streamlist = prs.run_frs(model, geom, npts=npts, dt=dt)
    >>> type(streamlist[0])
    <class 'obspy.core.stream.Stream'>
    >>> print(st)
    3 Trace(s) in Stream:
    3 Trace(s) in Stream:
    .synt..BHN | 2020-11-30T21:04:43.890339Z - 2020-11-30T21:05:21.365339Z | 40.0 Hz, 1500 samples
    .synt..BHE | 2020-11-30T21:04:43.891418Z - 2020-11-30T21:05:21.366418Z | 40.0 Hz, 1500 samples
    .synt..BHZ | 2020-11-30T21:04:43.891692Z - 2020-11-30T21:05:21.366692Z | 40.0 Hz, 1500 samples   
    >>> st.plot(size=(600, 450))

    """

    args = AttribDict(**locals())

    kwlist = ['model', 'geometry', 'verbose', 'wvtype', 'mults',
              'npts', 'dt', 'align', 'shift', 'rot', 'rf']

    for k in args:
        if k not in kwlist:
            raise(Exception('Incorrect kwarg: ', k))

    if shift is None:
        shift = dt

    if args.rf and (args.rot == 0):
        raise(Exception("The argument 'rot' cannot be '0'"))

    tr_ph, _ = fraysum.call_seis_spread(
            model.fthickn, model.frho, model.fvp, model.fvs, model.fflag,
            model.fani, model.ftrend, model.fplunge, model.fstrike, model.fdip,
            model.nlay,
            geometry.fbaz, geometry.fslow, geometry.fdx, geometry.fdy, geometry.ntr,
            wvtype, mults, npts, dt, align, shift, rot, int(verbose))

    # Read all traces and store them into a list of :class:`~obspy.core.Stream`
    streams = read_traces(tr_ph, geom=geometry.geom, dt=dt, rot=rot, shift=shift,
                          npts=npts, ntr=geometry.ntr)

    # Store everything into StreamList object
    streamlist = StreamList(model=model, geom=geometry.geom, streams=streams, args=args)

    if rf:
        streamlist.calculate_rfs()

    return streamlist
