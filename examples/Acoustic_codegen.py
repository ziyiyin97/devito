# coding: utf-8
from __future__ import print_function

import numpy as np

from devito.interfaces import DenseData
from examples.fwi_operators import *


class Acoustic_cg:
    """ Class to setup the problem for the Acoustic Wave
        Note: s_order must always be greater than t_order
    """
    def __init__(self, model, data, dm_initializer=None, source=None, nbpml=40, t_order=2, s_order=2):
        self.model = model
        self.t_order = t_order
        self.s_order = s_order
        self.data = data
        self.dtype = np.float32
        self.dt = model.get_critical_dt()
        self.h = model.get_spacing()
        self.nbpml = nbpml
        dimensions = self.model.get_shape()
        pad_list = []

        for dim_index in range(len(dimensions)):
            pad_list.append((nbpml, nbpml))

        self.model.vp = np.pad(self.model.vp, tuple(pad_list), 'edge')
        self.data.reinterpolate(self.dt)
        self.nrec, self.nt = self.data.traces.shape
        self.model.set_origin(nbpml)
        self.dm_initializer = dm_initializer

        if source is not None:
            self.source = source.read()
            self.source.reinterpolate(self.dt)
            source_time = self.source.traces[0, :]

            while len(source_time) < self.data.nsamples:
                source_time = np.append(source_time, [0.0])

            self.data.set_source(source_time, self.dt, self.data.source_coords)

        def damp_boundary(damp):
            h = self.h
            dampcoeff = 1.5 * np.log(1.0 / 0.001) / (40 * h)
            nbpml = self.nbpml
            num_dim = len(damp.shape)

            for i in range(nbpml):
                pos = np.abs((nbpml-i)/float(nbpml))
                val = dampcoeff * (pos - np.sin(2*np.pi*pos)/(2*np.pi))

                if num_dim == 2:
                    damp[i, :] += val
                    damp[-(i + 1), :] += val
                    damp[:, i] += val
                    damp[:, -(i + 1)] += val
                else:
                    damp[i, :, :] += val
                    damp[-(i + 1), :, :] += val
                    damp[:, i, :] += val
                    damp[:, -(i + 1), :] += val
                    damp[:, :, i] += val
                    damp[:, :, -(i + 1)] += val
        self.m = DenseData(name="m", shape=self.model.vp.shape, dtype=self.dtype)
        self.m.data[:] = self.model.vp**(-2)

        self.damp = DenseData(name="damp", shape=self.model.vp.shape, dtype=self.dtype)

        # Initialize damp by calling the function that can precompute damping
        damp_boundary(self.damp.data)

        self.src = SourceLike(name="src", npoint=1, nt=self.nt, dt=self.dt, h=self.h,
                              coordinates=np.array(self.data.source_coords, dtype=self.dtype)[np.newaxis, :],
                              ndim=len(dimensions), dtype=self.dtype, nbpml=nbpml)
        self.rec = SourceLike(name="rec", npoint=self.nrec, nt=self.nt, dt=self.dt, h=self.h,
                              coordinates=self.data.receiver_coords, ndim=len(dimensions), dtype=self.dtype,
                              nbpml=nbpml)
        self.src.data[:] = self.data.get_source()[:, np.newaxis]

        self.u = TimeData(name="u", shape=self.m.shape, time_dim=self.src.nt, time_order=t_order,
                          save=True, dtype=self.m.dtype)
        self.srca = SourceLike(name="srca", npoint=1, nt=self.nt, dt=self.dt, h=self.h,
                               coordinates=np.array(self.data.source_coords, dtype=self.dtype)[np.newaxis, :],
                               ndim=len(dimensions), dtype=self.dtype, nbpml=nbpml)
        if dm_initializer is not None:
            self.dm = DenseData(name="dm", shape=self.model.vp.shape, dtype=self.dtype)
            self.dm.data[:] = np.pad(dm_initializer, tuple(pad_list), 'edge')

    def Forward(self):
        fw = ForwardOperator(
            self.m, self.src, self.damp, self.rec, self.u, time_order=self.t_order, spc_order=self.s_order)
        fw.apply()

        return (self.rec.data, self.u.data)

    def Adjoint(self, rec):
        self.rec.data[:] = rec
        adj = AdjointOperator(self.m, self.rec, self.damp, self.srca, time_order=self.t_order, spc_order=self.s_order)
        v = adj.apply()[0]

        return (self.srca.data, v)

    def Gradient(self, rec, u):
        self.u.data[:] = u
        self.rec.data[:] = rec
        grad_op = GradientOperator(self.u, self.m, self.rec, self.damp, time_order=self.t_order, spc_order=self.s_order)
        dt = self.dt
        grad = grad_op.apply()[0]

        return (dt**-2)*grad

    def Born(self):
        born_op = BornOperator(
            self.dm, self.m, self.src, self.damp, self.rec, time_order=self.t_order, spc_order=self.s_order)
        born_op.apply()

        return self.rec.data

    def run(self):
        print('Starting forward')
        rec, u = self.Forward()

        res = rec - np.transpose(self.data.traces)
        f = 0.5*np.linalg.norm(res)**2

        print('Residual is ', f, 'starting gradient')
        g = self.Gradient(res, u)

        return f, g[self.nbpml:-self.nbpml, self.nbpml:-self.nbpml]
