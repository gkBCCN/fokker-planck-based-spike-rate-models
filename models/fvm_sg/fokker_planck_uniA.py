'''neccessary funcitons and classes to run the scharfetter gummel discretization
of the fokker planck equation. Different modes are possible: feedforward, recurrent
and also finite size effects are included'''

from __future__ import print_function
import time
import numpy as np
import scipy.sparse
import scipy.sparse.linalg
from math import log, sqrt, exp
from scipy.linalg import solve_banded
# try to import numba
# or define dummy decorator
try:
    from numba import njit
except:
    def njit(func):
        return func

print('TODO: change this with new fp solver; fokker_planck_update.py')
print('importing fvm_sg module...')



class Grid(object):
    '''
    This class implements the V discretization, which is used for both the
    Scharfetter-Gummel method and the upwind implementation
    '''

    def __init__(self, V_0=-200., V_1=-40., V_r=-70., N_V=100):
        self.V_0 = V_0
        self.V_1 = V_1
        self.V_r = V_r
        self.N_V = int(N_V)
        # construct the grid object
        self.construct()

    def construct(self):
        self.V_centers = np.linspace(self.V_0, self.V_1, self.N_V)
        # shift V_centers by half of the grid spacing to the left
        # such that the last interface lies exactly on V_l
        self.V_centers -= (self.V_centers[-1] - self.V_centers[-2]) / 2.
        self.dV_centers = np.diff(self.V_centers)
        self.V_interfaces = np.zeros(self.N_V + 1)
        self.V_interfaces[1:-1] = self.V_centers[:-1] + 0.5 * self.dV_centers
        self.V_interfaces[0] = self.V_centers[0] - 0.5 * self.dV_centers[0]
        self.V_interfaces[-1] = self.V_centers[-1] + 0.5 * self.dV_centers[-1]
        self.dV_interfaces = np.diff(self.V_interfaces)
        self.dV = self.V_interfaces[2] - self.V_interfaces[1]
        self.ib = np.argmin(np.abs(self.V_centers - self.V_r))
# DRIFT COEFFICIENTS
@njit
def get_v_numba(L, Vi, DT, EL, VT, taum, mu, EIF = True):
    '''drift coeffs for EIF/PIF model, depending on EIF = True/False'''
    # EIF model
    drift = np.empty(L)
    if EIF:
        for i in xrange(L):
            drift[i] = ((EL - Vi[i]) + DT * exp((Vi[i] - VT) / DT)) / taum + mu
    # PIF model
    else:
        for i in xrange(L):
            drift[i] = mu
    drift[np.where(drift == 0.)] = 10e-15
    return drift
def get_v(grid, mu, params):
        '''returns the coefficients for the drift part of the flux for different neuron
         models. At the moment only for exponential-integrate-and-fire (EIF)
          and perfect-integrate-and-fire (PIF) '''
        Vi = grid.V_interfaces
        if params['neuron_model'] == 'EIF':
            EL = params['EL']
            taum = params['taum']
            DT = params['deltaT']
            VT = params['VT']
            #THIS TERM WILL LATER EXPLICITLY CONTAIN AN ADAPTATION CURRENT
            drift = ((EL - Vi) + DT * np.exp((Vi - np.ones_like(Vi) * VT) / DT)) / taum + mu
            # idea for Problem v --> 0: set all zero values of the drift array to mininmum value
            drift[np.where(drift == 0.)] = 10e-15
        elif params['neuron_model'] == 'PIF':
            drift = np.ones_like(Vi) * mu
            drift[np.where(drift == 0.)] = 10e-15
        else:
            err_mes = 'The model "{}" has not been implemented yet. For options see params dict.'.format(params['neuron_model'])
            raise NotImplementedError(err_mes)
        return drift
@njit
def get_diagonals_sg(dim_p, v, dV, dt, D):
    diag = np.empty(dim_p)
    lower = np.empty(dim_p - 1)
    upper = np.empty(dim_p - 1)
    for i in xrange(1, dim_p - 1):
        diag[i] = dV/dt + ((v[i + 1])) * (1. / (1. - exp((-v[i + 1] * dV) / D))) + ((v[i])) * (
        exp((-v[i] * dV) / D) / (1. - exp((-v[i] * dV) / D)))
        lower[i - 1] = -((v[i])) * (1. / (1. - exp((-v[i] * dV) / D)))
        upper[i] = -((v[i + 1])) * (exp((-v[i + 1] * dV) / D) / (1. - exp((-v[i + 1] * dV) / D)))

    diag[0] = dV/dt + ((v[1])) * (1. / (1. - exp((-v[1] * dV) / D)))
    upper[0] = -((v[1])) * ((exp((-v[1] * dV) / D)) / (1. - exp((-v[1] * dV) / D)))

    diag[-1] = dV/dt + ((v[-2])) * ((exp((-v[-2] * dV) / D)) / (1. - exp((-v[-2] * dV) / D))) + ((v[-1])) * ((1. + exp((-v[-1] * dV) / D)) / (1. - exp((-v[-1] * dV) / D)))
    lower[0] = -((v[1])) * (1. / (1. - exp((-v[1] * dV) / D)))
    lower[-1] = -((v[-2])) * (1. / (1. - exp((-v[-2] * dV) / D)))
    return (upper, diag, lower)




import math
#####################
# helper function for diags_A
@njit
def exp_vdV_D(v,dV,D):
    return math.exp(-v*dV/D)


# this is a new function which fills the three diagonals of a matrix A
# this matrix can be used in both versions of this solver.
# 1) banded solve: (1I - A*dt)p(t+dt)=p(t)
# 2) exponential integrator: p(t+dt)=exp(Adt)p(t) + inhom....

# len(p) = N
# len(v) = N+1


def diags_A(u,d,l,N,v,D,dV):
    # todo check this
    dV_inv = 1./dV

    # Note even though in Matrix A diag,sub,sup
    # do not have the same shape, here we use
    # arrays of equal length for all of them
    # because later we stack a NX3 matrix with them!

    #fill diag
    # element first and last elemet get overwritten for boundary conditions
    for i in xrange(1,N-1):
        # diag[i] = -dV_inv*(v[i]*exp_vdV_D(v[i],dV,D)/(1.-exp_vdV_D(v[i],dV,D))
        #              +v[i+1]/exp_vdV_D(v[i+1],dV,D))
        d[i] = -dV_inv*(v[i]*exp_vdV_D(v[i],dV,D)/(1.-exp_vdV_D(v[i],dV,D))
                           +v[i+1]/(1.-exp_vdV_D(v[i+1],dV,D)))
        # print(i)

    # fill sub diag
    for i in xrange(1,N-1):
        # first element of sub corresponds to p1
        # --> v_{-} is in this case v[1]
        l[i-1] = dV_inv*v[i]/(1.-exp_vdV_D(v[i],dV,D))
        # print(i)

    # fill sup
    for i in xrange(1,N-1):
        # sup[i+1] = v[i+1]*exp_vdV_D(v[i+1],dV,D)/(1.-exp_vdV_D(v[i+1],dV,D))
        u[i+1] = dV_inv*v[i+1]*exp_vdV_D(v[i+1],dV,D)/(1.-exp_vdV_D(v[i+1],dV,D))
        # print(i)


    # boundary conditions
    # reflecting
    # d[0] = -dV_inv*(v[1]/(1.-exp_vdV_D(v[1],dV,D)))
    d[0] = -dV_inv*(v[1]/(1.-exp_vdV_D(v[1],dV,D)))
    u[1] = dV_inv*(v[1]*exp_vdV_D(v[1],dV,D)/(1.-exp_vdV_D(v[1],dV,D)))

    # u[1] = dV_inv*v[1]*exp_vdV_D(v[1],dV,D)/(1.-exp_vdV_D(v[1],dV,D))

    # absorbing
    # d[-1] = -dV_inv*( v[-2]*exp_vdV_D(v[-2],dV,D)/(1.-exp_vdV_D(v[-2],dV,D))
    #              + v[-1]*(1.+exp_vdV_D(v[-1],dV,D))/(1.-exp_vdV_D(v[-1],dV,D)))
    # l[-2] = dV_inv*v[-2]/(1.-exp_vdV_D(v[-2],dV,D))
    ##################################################################
    l[-2] = dV_inv*v[-2]/(1.-exp_vdV_D(v[-2],dV,D))
    d[-1] = -dV_inv*(v[-2]*exp_vdV_D(v[-2],dV,D)/(1.-exp_vdV_D(v[-2],dV,D))
                     +v[-1]*(1.+exp_vdV_D(v[-1],dV,D))/(1.-exp_vdV_D(v[-1],dV,D)))

@njit
def matAdt(mat,N,v,D,dV,dt):
    # todo check this
    dt_dV = dt/dV

    # Note even though in Matrix A diag,sub,sup
    # do not have the same shape, here we use
    # arrays of equal length for all of them
    # because later we stack a NX3 matrix with them!

    #fill diag
    # element first and last elemet get overwritten for boundary conditions
    for i in xrange(1,N-1):
        # diag[i] = -dV_inv*(v[i]*exp_vdV_D(v[i],dV,D)/(1.-exp_vdV_D(v[i],dV,D))
        #              +v[i+1]/exp_vdV_D(v[i+1],dV,D))
        mat[1,i] = -dt_dV*(v[i]*exp_vdV_D(v[i],dV,D)/(1.-exp_vdV_D(v[i],dV,D))
                           +v[i+1]/(1.-exp_vdV_D(v[i+1],dV,D)))
        # print(i)

    # fill sub diag
    for i in xrange(1,N-1):
        # first element of sub corresponds to p1
        # --> v_{-} is in this case v[1]
        mat[2,i-1] = dt_dV*v[i]/(1.-exp_vdV_D(v[i],dV,D))
        # print(i)

    # fill sup
    for i in xrange(1,N-1):
        # sup[i+1] = v[i+1]*exp_vdV_D(v[i+1],dV,D)/(1.-exp_vdV_D(v[i+1],dV,D))
        mat[0,i+1] = dt_dV*v[i+1]*exp_vdV_D(v[i+1],dV,D)/(1.-exp_vdV_D(v[i+1],dV,D))
        # print(i)


    # boundary conditions
    # reflecting
    # d[0] = -dV_inv*(v[1]/(1.-exp_vdV_D(v[1],dV,D)))
    mat[1,0] = -dt_dV*(v[1]/(1.-exp_vdV_D(v[1],dV,D)))
    mat[0,1] = dt_dV*(v[1]*exp_vdV_D(v[1],dV,D)/(1.-exp_vdV_D(v[1],dV,D)))

    # u[1] = dV_inv*v[1]*exp_vdV_D(v[1],dV,D)/(1.-exp_vdV_D(v[1],dV,D))

    # absorbing
    # d[-1] = -dV_inv*( v[-2]*exp_vdV_D(v[-2],dV,D)/(1.-exp_vdV_D(v[-2],dV,D))
    #              + v[-1]*(1.+exp_vdV_D(v[-1],dV,D))/(1.-exp_vdV_D(v[-1],dV,D)))
    # l[-2] = dV_inv*v[-2]/(1.-exp_vdV_D(v[-2],dV,D))
    ##################################################################
    mat[2,-2] = dt_dV*v[-2]/(1.-exp_vdV_D(v[-2],dV,D))
    mat[1,-1] = -dt_dV*(v[-2]*exp_vdV_D(v[-2],dV,D)/(1.-exp_vdV_D(v[-2],dV,D))
                     +v[-1]*(1.+exp_vdV_D(v[-1],dV,D))/(1.-exp_vdV_D(v[-1],dV,D)))

@njit
def matAdt_opt(mat,N,v,D,dV,dt):
    # todo check this
    dt_dV = dt/dV

    # Note even though in Matrix A diag,sub,sup
    # do not have the same shape, here we use
    # arrays of equal length for all of them
    # because later we stack a NX3 matrix with them!
    # todo| I have already taken care that v is never 0,
    # todo| but could be implemented less hacky
    for i in xrange(1,N-1):
        # diagonal
        mat[1,i] = -dt_dV*(v[i]*exp_vdV_D(v[i],dV,D)/(1.-exp_vdV_D(v[i],dV,D))
                           +v[i+1]/(1.-exp_vdV_D(v[i+1],dV,D)))
        # lower diagonal
        mat[2,i-1] = dt_dV*v[i]/(1.-exp_vdV_D(v[i],dV,D))
        # upper diagonal
        mat[0,i+1] = dt_dV*v[i+1]*exp_vdV_D(v[i+1],dV,D)/(1.-exp_vdV_D(v[i+1],dV,D))

    # boundary conditions
    # first diagonal
    mat[1,0] = -dt_dV*(v[1]/(1.-exp_vdV_D(v[1],dV,D))) # check
    # first upper
    mat[0,1] = dt_dV * v[1]*exp_vdV_D(v[1],dV,D)/(1.-exp_vdV_D(v[1],dV,D)) #check
    # last lower
    mat[2,-2] = dt_dV*v[-2]/(1.-exp_vdV_D(v[-2],dV,D)) #check
    # last diagonal
    mat[1,-1] = -dt_dV*(v[-2]*exp_vdV_D(v[-2],dV,D)/(1.-exp_vdV_D(v[-2],dV,D))
                     +v[-1]*(1.+exp_vdV_D(v[-1],dV,D))/(1.-exp_vdV_D(v[-1],dV,D)))

# initial probability distribution
def initial_p_distribution(grid,params):
    if params['fp_v_init'] == 'normal':
        mean_gauss = params['fp_normal_mean']
        sigma_gauss = params['fp_normal_sigma']
        p_init = np.exp(-np.power((grid.V_centers - mean_gauss), 2) / (2 * sigma_gauss ** 2))
    elif params['fp_v_init'] == 'delta':
        delta_peak_index = np.argmin(np.abs(grid.V_centers - params['fp_delta_peak']))
        p_init = np.zeros_like(grid.V_centers)
        p_init[delta_peak_index] = 1.
    elif params['fp_v_init'] == 'uniform':
        # uniform dist on [Vr, Vcut]
        p_init = np.zeros_like(grid.V_centers)
        p_init[grid.ib:] = 1.
    else:
        err_mes = ('Initial condition "{}" is not implemented! See params dict for options.').format(params['fp_v_init'])
        raise NotImplementedError(err_mes)
    # normalization with respect to the cell widths
    p_init =p_init/np.sum(p_init*grid.dV_interfaces)
    return p_init

# current version for model comparison
# currently without FS effects until everything is tested
def sim_fp_sg(input, params, rec = False, FS=True):#, timing=None):
    '''solving the fp equation using the scharfetter gummel method'''

    # default timing is None
    # else: Number of time measurements
    # if timing is not None:
    #     times_expm = np.empty(timing)
    #     times_convert_mat = np.empty(timing)
    #    times_banded_solve = np.empty(timing)


    # print('running sg scheme')


    # time domain
    # get time step; different ones for
    # implicit scheme and exponential integrator
    integration_method = params['integration_method']
    if integration_method == 'implicit':
        dt = params['fp_dt_implicit']
    elif integration_method in ['expmPhi','expmEuler']:
        dt = params['fp_dt_expm']
    else:
        raise NotImplementedError

    runtime = params['runtime']
    steps = int(runtime/dt)
    inform = int(steps/10)
    t = np.linspace(0., runtime, steps+1)


    # external input
    mu_ext = input[0]
    sigma_ext = input[1]
    # ensure that input is transformed to array if int, float
    if type(mu_ext) in [int,float]:
        mu_ext = np.ones_like(t)*mu_ext
    if type(sigma_ext) in [int,float]:
        sigma_ext = np.ones_like(t)*sigma_ext


    t_ref = max(params['t_ref'], dt) # ensure that t_ref is at least a timestep
    n_ref = int(t_ref/dt)
    DT = params['deltaT']
    EL = params['EL']
    VT = params['VT']
    taum = params['taum']
    C = params['C']

    # params for adaptation
    a = params['a']
    b = params['b']
    # convert to array if adapt params are scalar values
    if type(a) in [int,float]:
        a = np.ones_like(mu_ext)*a
    if type(b) in [int,float]:
        b = np.ones_like(mu_ext)*b
    have_adap = True if (a.any() or b.any()) else False
    print('have adapt: {}'.format(have_adap))
    Ew = params['Ew']
    tauw = params['tauw']
    b_tauw=b*tauw
    EIF_model = True if params['neuron_model'] == 'EIF' else False

    # for coupled/recurrent populations/networks
    K = int(params['K'])
    J = params['J']
    delay_type = params['delay_type']
    n_d = int(params['const_delay']/dt) #get index shift for recurrent rate
    taud = params['taud']



    # instance of the spatial grid class
    grid = Grid(V_0=params['Vlb'], V_1=params['Vcut'], V_r=params['Vr'],
                N_V=params['N_centers_fp'])




    # FS stuff
    # if FS:
    N = params['N_total']
    noise_type = params['noise_type']
    N_dt = N*dt
    norm_type = params['norm_type']
    force_normalization = (norm_type is not None) and FS






    # initial density
    p0 = initial_p_distribution(grid, params)


    # initialize arrays
    r   = np.zeros_like(t)
    rN = np.zeros_like(t)
    r_d = np.zeros_like(t)
    mu_tot    = np.zeros_like(t)
    mu_syn    = np.zeros_like(t)
    sigma_tot = np.zeros_like(t)
    int_P     = np.zeros_like(t)
    int_ref   = np.zeros_like(t)
    mass_timecourse = np.zeros_like(t)
    Vmean = np.zeros_like(t)
    wm     = np.zeros_like(t)

    # initial values
    wm0 = params['wm_init']
    wm[0] =wm0
    r_rec = 0


    # optimizations
    dV_dt = grid.dV/dt
    dV = grid.dV
    p = p0
    N_V = grid.N_V

    # data matrix Adt which can be used for two integration methods:
    # 1) (1I-Adt)p(t+dt)=p(t) [implicit]
    # 2) p(t+dt)=p(t)exp(Adt) + '' [exponential (phi function)]
    # 3) p(t+dt)=p(t)exp(Adt) + '' [exponential (euler method)]

    Adt = np.zeros((3,grid.N_V))
    reinject_ib = np.zeros(grid.N_V); reinject_ib[grid.ib] = 1.



    for n in xrange(steps):

        # print sim time information

        # calculate mass inside and outside the comp. domain
        int_P[n] = np.sum(p*dV)
        int_ref[n] = np.sum(r[n-n_ref:n]*dt)
        # normalize the density in the inner domain
        #todo change this
        p_marg = p/int_P[n]
        # calculate the mean membrane voltage
        Vmean[n] = np.sum(p_marg*grid.V_centers*dV)
        mass_timecourse[n] = int_P[n] + int_ref[n]


        if force_normalization:
            if norm_type == 'full_domain':
                p*=(1.-int_ref[n])/int_P[n]
            else:
                raise NotImplementedError

        # print out some information
        if n%inform==0:
             print('simulated {}%, total mass: {}'.
                   format((float(n)*100/steps), mass_timecourse[n]))


        # mu_syn (synaptic) combines the external input with recurrent input
        mu_syn[n] = mu_ext[n] if not rec else K * J * r_rec + mu_ext[n]
        sigma_tot[n] = sigma_ext[n] if not rec \
            else sqrt(K * J ** 2 * r_rec + sigma_ext[n] ** 2)




        # compute mu_tot from mu_syn and mean adaptation
        mu_tot[n] = mu_syn[n] - wm[n]/C if have_adap else mu_syn[n]

        v = get_v_numba(grid.N_V+1, grid.V_interfaces, DT, EL, VT,
                        taum, mu_tot[n], EIF = EIF_model)
        D = (sigma_tot[n] ** 2) * 0.5


        # correct units of the reinjection
        # reinjection, add value to the rhs of the linear system
        # construct the linear system
        # (up, dia, low) = get_diagonals_sg(grid.N_V, v, dV, dt, D)
        # now we have the new diagonals
        # diags_A(u,d,l,grid.N_V,v,D,dV)

        # create banded matrix A
        matAdt_opt(Adt,grid.N_V,v,D,dV,dt)

        # chose between three different integration
        # method for time discretization

        # implicit Euler
        if integration_method == 'implicit':
            # compute (1I - Adt)
            #d = np.ones_like(d) - d*dt
            #u = -dt*u
            #l = -dt*l
            ## assemble matrix in banded form
            #mat_band = np.matrix([u,d,l])
            ## note: the outflux[n] is generated by P_n+1:
            ## for each time point we have a density. the outflux between these time-points
            ## is outflux[n]
            #p_new = solve_banded((1, 1), mat_band, rhs)
            # compute (1I - Adt)

            rhs = p.copy()
            reinjection = r[n-n_ref]*(dt/dV) if not FS else rN[n-n_ref]*(dt/dV)
            rhs[grid.ib] += reinjection
            Adt *= -1.
            Adt[1,:] += np.ones(grid.N_V)
            # note: the outflux[n] is generated by P_n+1:
            # for each time point we have a density. the outflux between these time-points
            # s = time.time()
            p_new = solve_banded((1, 1), Adt, rhs)
            # duration = time.time()-s
            # times_banded_solve[n] = duration
            # times_expm[n] = 0.
            # times_convert_mat[n] = 0.


        elif integration_method == 'expmPhi':
            # 1)
            # calculate y
            reinjection = r[n-n_ref]/dV if not FS else rN[n-n_ref]/dV
            rhs = reinject_ib*reinjection
            A = Adt/dt
            y = solve_banded((1,1), A, rhs)
            # 2)
            # solve expm_multiply(p,y)
            offsets = np.array([1, 0, -1])
            Adt_dia = scipy.sparse.dia_matrix((Adt, offsets),
                    shape=(grid.N_V,grid.N_V))
            Adt_csr = Adt_dia.tocsr() #scipy.sparse.csr_matrix(A_dia)
            # changed to np.column_stack because np.stack does not work on merope
            # py = np.stack((p,y),axis=1)
            py = np.column_stack((p,y))

            expm_Adt_py = scipy.sparse.linalg.expm_multiply(Adt_csr,py)
            expm_Adt_p = expm_Adt_py[:,0]
            expm_Adt_y = expm_Adt_py[:,1]
            p_new = expm_Adt_p+expm_Adt_y-y

        # fastest exponential integrator version
        elif integration_method == 'expmEuler':
            # first convert mat to sparse-mat
            offsets = np.array([1, 0, -1])
            # s = time.time()
            Adt_dia = scipy.sparse.dia_matrix((Adt, offsets),
                    shape=(N_V,N_V))
            Adt_csr = Adt_dia.tocsr()
            # duration = time.time()-s
            # times_convert_mat[n] = duration
            # devide by dV in order to get the units correctly
            reinject_ib[grid.ib] = r[n-n_ref]/dV if not FS else rN[n-n_ref]/dV

            # solve banded system for inhomogenous part
            # s=time.time()
            p_new = scipy.sparse.linalg.expm_multiply(Adt_csr, p+dt*reinject_ib)
            # duration = time.time()-s
            # times_expm[n] = duration
            # times_banded_solve[n] =0.
        else:
            raise NotImplementedError
        # if n == timing-1:
        #     break

        # compute rate
        r[n] = (v[-1]*((1.+exp((-v[-1]*dV)/D))/(1.-exp((-v[-1]*dV)/D)))*p_new[-1])

        # if no FS, use this for the adaptation equation
        r_adapt = r[n]

        if FS:
            if noise_type == 'poisson':
                rN[n] = np.random.poisson(N_dt*r[n])/N_dt
                r_adapt=rN[n]

            elif noise_type == 'gauss':
                rN[n] = r[n]+sqrt(r[n]/N_dt)*np.random.randn()
                r_adapt=rN[n]
            else:
                raise NotImplementedError\
                    ('noise type {} was not implemented!'.
                     format(params['noise_type']))


        # euler step for adaptation current
        wm[n+1] = wm[n] + (dt/tauw)*(a[n]*(Vmean[n]-Ew)-wm[n]+b_tauw[n]*r_adapt)


        # get recurrent rate r_rec
        if rec:
            if delay_type == 0:
                r_rec = r[n]
            # const. delay of n_d*dt [ms]
            elif delay_type == 1:
                r_rec = r[n-n_d]
            # exp. distributed delays
            elif delay_type == 2:
                # solve ODE for rd: d_rd/d_t = r-rd/taud
                r_diff = (r[n] -r_d[n])
                r_d[n+1] = r_d[n] + dt * r_diff/taud
                r_rec = r_d[n]
            # exp. distributed delays + const, delay
            elif delay_type == 3:
                # solve delayed ODE for r_rec: d_rd/d_t = r(t-t_d)-rd/taud
                r_diff = (r[n-n_d] - r_d[n])
                r_d[n+1] = r_d[n] + dt * r_diff/taud
                r_rec = r_d[n]

        # overwrite P with updated P_new
        p = p_new

    # return time, rates and adaptation arrays
    results = {'t':t, 'r':r*1000, 'wm':wm, 'rN':rN*1000.}
    # if timing is not None:
    #     results['mean_convert_mat'] = np.mean(times_convert_mat)
    #     results['mean_expm'] = np.mean(times_expm)
    #     results['mean_banded_solve'] = np.mean(times_banded_solve)
    return results

# version which saves all Ps over time
def sim_fp_sg_save_P_over_Time(input, params):
    '''solving the fp equation using the scharfetter gummel method'''
    print('running sg scheme')

    # external input
    mu_ext = input[0]
    sigma_val = input[1]
    sigma_ext = np.ones_like(mu_ext)*sigma_val

    # unpack parameter dict
    dt = params['dt_fp']
    t_ref = max(params['t_ref'],dt) # ensure that t_ref is at least a timestep
    n_ref = int(t_ref/dt)
    DT = params['deltaT']
    EL = params['EL']
    VT = params['VT']
    taum = params['taum']
    C = params['C']

    # params for adaptation
    a = params['a']
    b = params['b']
    have_adap = True if (a.any() or b.any()) else False
    have_adap = True
    Ew = params['Ew']
    tauw = params['tauw']
    EIF_model = True if params['neuron_model'] == 'EIF' else False


    # time domain
    runtime = params['runtime']
    steps = int(runtime/dt)
    inform = int(steps/10)
    t = np.linspace(0., runtime, steps+1)

    # in case time step of the fokker-planck
    # implementation is smaller then the one of the input
    if dt < params['uni_dt']:
        mu_ext = np.repeat(mu_ext, int(params['uni_dt']/dt))
        sigma_ext = np.repeat(sigma_ext, int(params['uni_dt']/dt))

    #
    K = int(params['K'])
    J = params['J']
    delay_type = params['delay_type']
    n_d = int(params['const_delay']/dt) #get index shift for recurrent rate
    taud = params['taud']





    # instance of the spatial grid class
    grid = Grid(V_0=params['Vlb'], V_1=params['Vcut'], V_r=params['Vr'],
                N_V=params['N_centers_fp'])


    # initial density
    P_init = initial_p_distribution(grid, params)


    # initialize arrays
    P_save = np.zeros((steps+1, grid.N_V))
    mu_tot    = np.zeros_like(t)
    mu_syn    = np.zeros_like(t)
    sigma_tot = np.zeros_like(t)
    int_P     = np.zeros_like(t)
    int_ref   = np.zeros_like(t)
    r   = np.zeros_like(t)
    mass_timecourse = np.zeros_like(t)
    Vmean = np.zeros_like(t)
    wm     = np.zeros_like(t)

    dV = grid.dV
    P0 = np.copy(P_init)
    mu_tot0 = mu_ext[0]
    sigma_tot0 = sigma_ext[0]
    v = get_v_numba(grid.N_V+1, grid.V_interfaces, DT, EL, VT,
                        taum, mu_tot0, EIF = EIF_model)
    D = (sigma_tot0 ** 2) * 0.5
    r0 = (v[-1]*((1.+exp((-v[-1]*dV)/D))/(1.-exp((-v[-1]*dV)/D)))*P0[-1])
    wm0 = params['wm_init']
    wm[0] =wm0


    # optimizations
    dV_dt = grid.dV/dt
    dV = grid.dV
    P_save[0,:] = P0
    r[0]  = r0


    for n in xrange(steps):

        # print sim time information

        # calculate mass inside and outside the comp. domain
        int_P[n] = np.sum(P_save[n,:]*dV)
        int_ref[n] = np.sum(r[n-n_ref:n]*dt)
        # normalize the density in the inner domain
        P_marg = P_save[n, :]/int_P[n]
        # calculate the mean membrane voltage
        Vmean[n] = np.sum(P_marg*grid.V_centers*dV)
        mass_timecourse[n] = int_P[n] + int_ref[n]



        if n%inform==0:
            print('simulated {}%, total mass: {}'.
                  format((float(n)*100/steps),mass_timecourse[n]))
        # mu_syn (synaptic) combines the external input with recurrent input
        mu_syn[n] = mu_ext[n]
        sigma_tot[n] = sigma_ext[n]




        # compute mu_tot from mu_syn and mean adaptation
        mu_tot[n] = mu_syn[n] - wm[n]/C if have_adap else mu_syn[n]

        v = get_v_numba(grid.N_V+1, grid.V_interfaces, DT, EL, VT,
                        taum, mu_tot[n], EIF = EIF_model)
        D = (sigma_tot[n] ** 2) * 0.5


        rhs = np.copy(P_save[n,:])*dV_dt

        # reinject the outflux(t-t_ref)
        reinjection = r[n-n_ref]
        # reinjection, add value to the rhs of the linear system
        rhs[grid.ib] += reinjection
        # construct the linear system
        (up, dia, low) = get_diagonals_sg(grid.N_V, v, dV, dt, D)
        ud = np.insert(up, 0, 0)
        ld = np.insert(low, grid.N_V - 1, 0)
        # assemble matrix in banded form
        mat_band = np.matrix([ud, dia, ld])



        # note: the outflux[n] is generated by P_n+1:
        # for each time point we have a density. the outflux between these time-points
        # is outflux[n]
        P_save[n+1,:] = solve_banded((1, 1), mat_band, rhs)
        # figure out what this problem was :-) not so clear !!!
        r[n] = (v[-1]*((1.+exp((-v[-1]*dV)/D))/(1.-exp((-v[-1]*dV)/D)))*P_save[n+1,-1])
        # euler step for integration
        wm[n+1] = wm[n] + (dt/tauw)*(a[n]*(Vmean[n]-Ew)-wm[n]+b[n]*tauw*r[n])

    # return time, rates and adaptation arrays
    results = {'t':t, 'r':r*1000, 'wm':wm}
    return results


##############################################
# old version of the solver which was in use so far
def sim_fp_sg_OLD(input, params, save_densities=None, FS=False, rec=False):
    '''solving the fp equation using the scharfetter gummel method'''
    print('running sg scheme')

    # external input
    mu_ext = input[0]
    sigma_val = input[1]
    sigma_ext = np.ones_like(mu_ext)*sigma_val

    # unpack parameters
    tau = params['t_ref']
    DT = params['deltaT']
    EL = params['EL']
    VT = params['VT']
    taum = params['taum']
    C = params['C']
    dt = params['dt_fp']
    # params for adaptation
    a = params['a']
    b = params['b']
    Ew = params['Ew']
    tauw = params['tauw']
    have_adapt = False#True if a or b else False


    EIF_model = True if params['neuron_model'] == 'EIF' else False
    sqrt_dt = sqrt(dt)
    n_ref = int(tau / dt)
    dt_tauw = dt/tauw


    # time domain
    runtime = params['runtime']
    steps = int(runtime/dt)
    time = np.arange(0., runtime + dt, dt)

    #todo: remove this hack
    #if so dt < params['uni_dt'] manipulate input
    if dt < params['uni_dt']:
        repetition=int(params['uni_dt']/dt)
        mu_ext = np.repeat(mu_ext, repetition)
        sigma_ext = np.repeat(sigma_ext, repetition)
        a = np.repeat(a, repetition)
        b = np.repeat(b, repetition)
    b_tauw = b*tauw

    # for recurrency
    K = int(params['K'])
    J = params['J']
    delay_type = params['delay_type']
    n_d = int(params['const_delay']/dt) #get index shift for recurrent rate
    taud = params['taud']



    # FOR NETWORK OF FINITE SIZE
    if FS:
        N = params['N_total']  # NUMBER OF NEURONS --> N = FINITE < INF

    # SPATIAL GRID
    grid = Grid(V_0=params['Vlb'], V_1=params['Vcut'], V_r=params['Vr'], N_V=params['N_centers_fp'])
    # FORCENORAMILZATION (ONLY IN CASE OF FINITE N)
    # CHECK IF SAME STRANGE BEHAVIOUR HAPPENS FOR SMALLER TIME STEP
    force_normalization = False and FS
    # INITIAL DENSITY
    # RETURNS NORMALIZED GAUSSIAN OR UNIFORM INITIAL DISTRIBUTION
    P_init = initial_p_distribution(grid, params)



    # INITIALIZE ARRAYS
    V_mean = np.zeros_like(time)
    rates = np.zeros_like(time)
    r_d = np.zeros_like(time)
    mu_tot = np.zeros_like(time)
    reinject = np.zeros_like(time)
    sigma_tot = np.zeros_like(time)
    outfluxes = np.zeros_like(time)
    mass_timecourse = np.zeros_like(time)
    # adaptation
    wm = np.zeros_like(time)
    p_time = np.zeros((params['N_centers'], time.size / save_densities)) if save_densities is not None else None
    rands = np.random.randn(time.size)

    # initial values of mass, time, P and the model dependent part of the drift coeffs
    mass_out = 0.
    T = 0.
    P = np.copy(P_init)

    for n in xrange(steps):
        # PRINT TIME IN 1000 ms STEPS
        if T % 100 < dt:
            print(T)

        rhs = (np.copy(P)*grid.dV)/dt

        # FOR REINJECTION MAKE EULER-MARUYAMA STEP
        # OUTFLUXES INITIALIZED WITH ZEROS --> ONLY AFTER TIMES LARGER THAN T_REF PROBABILITY MASS GETS REINJECTED!
        reinjection = outfluxes[n - n_ref]

        # REINJECTION OF THE ADDITIONALLY PRODUCED PROBABILITY MASS DUE TO THE FINITE SIZE OF THE NETWORK AT THE OUTFLUX
        #TODO CLEAR WHICH OF THE TWO MAKES MORE SENSE ##############################################################
        # reinjection += sqrt_dt * sqrt(outfluxes[n - n_ref] / N) * rands[n - n_ref] / grid.dV if FS else 0.
        reinjection += sqrt_dt * sqrt(outfluxes[n - n_ref] / N) * rands[n - n_ref] if FS else 0.
        #TODO ######################################################################################################

        #SAVE REINJECTIONS TO THE COMPUTATIONAL DOMAIN
        reinject[n] = reinjection
        rhs[grid.ib] += reinjection

        # TYPE OF RECURRENCY
        # SIMPLE CONSTANT DELAY

        # calculate mu_syn --> total synaptic input
        # no delay --> take most recent rate that is available rates[n]
        if delay_type == 0 and rec:
            # take most recent rate that is available
            r_rec = rates[n]
        elif delay_type == 1 and rec:
            #take rate shifted by index n_d
            r_rec = rates[n - n_d]
        # EULER-MARUYAMA METHOD FOR THE DELAYED RATE (r_d)
        # exponential delay distribution
        elif delay_type == 2 and rec:
            r_d[n + 1] = r_d[n] + dt * (outfluxes[n-1] - r_d[n]) / taud
            r_d[n + 1] += sqrt_dt * sqrt(outfluxes[n - 1] / N) * rands[n - 1] / taud if FS else 0.
            r_rec = r_d[n]
        # exponential delay distribution + const. delay
        elif delay_type == 3 and rec:
            r_d[n + 1] = r_d[n] + dt * (outfluxes[n - n_d] - r_d[n]) / taud
            r_d[n + 1] += sqrt_dt * sqrt(outfluxes[n - n_d] / N) * rands[n - n_d] / taud if FS else 0.
            r_rec = r_d[n]
        elif delay_type not in [0,1,2,3] and rec:
            err_mes = 'Delay type {} is not implemented.'.format(params['delay_type'])
            raise NotImplementedError(err_mes)

        # EXTERNAL INPUTS ARE COMBINED WITH RECURRENCY
        mu_tot[n] = mu_ext[n] if not rec else K * J * r_rec + mu_ext[n]
        sigma_tot[n] = sigma_ext[n] if not rec else sqrt(K * J ** 2 * r_rec + sigma_ext[n] ** 2)

        # drift and diffusion coeffs
        # the commented out function is slighty slower than its numba version below
        # v = get_v(grid, mu_tot[n], params)
        v = get_v_numba(grid.N_V+1, grid.V_interfaces, DT, EL, VT, taum, mu_tot[n] - wm[n]/C, EIF = EIF_model)
        D = (sigma_tot[n] ** 2) * 0.5


        # CONSTRUCT DIAGONALS OF THE LINEAR SYSTEM
        (up, dia, low) = get_diagonals_sg(grid.N_V, v, grid.dV, dt, D)
        ud = np.insert(up, 0, 0)
        ld = np.insert(low, grid.N_V - 1, 0)

        # ASSEMBLE MATRIX IN BANDED FORM IN ORDER TO SOLVE THE LINEAR SYSTEM EFFICIENTLY
        mat_band = np.matrix([ud, dia, ld])
        P = (solve_banded((1, 1), mat_band, rhs))

        # SAVE DENSITY OVER TIME
        if save_densities is not None and n % save_densities is False:
            p_time[:, n / save_densities] = P

        # TRACKING MASSES INSIDE AND OUTSIDE OF THE COMPUTATIONAL DOMAIN
        mass_out -= reinjection*dt
        mass_in = np.sum(P * grid.dV_interfaces)

        # CALCULATE OUTFLUX (SCHARFETTER GUMMEL DISCRETIZATION)
        outfluxes[n + 1] = (v[-1] * ((1. + exp((-v[-1] * grid.dV_interfaces[-1]) / D)) / (1. - exp((-v[-1] * grid.dV_interfaces[-1]) / D))) * P[-1])
        # assert outfluxes[n+1] > 0.

        if outfluxes[n+1] < 0.:
            plt.plot(grid.V_centers,P)
            plt.show()

        if FS:# CALCULATE THE NOISY RATE r_n FROM THE OUTFLUX
            r_N = outfluxes[n + 1] + sqrt(outfluxes[n + 1] / (N*dt)) * rands[n + 1]
        # IF FS TRUE, RATE BECOMES NOISY RATE, ELSE THIS IS THE FIRING RATE OF AN INFINITE NETWORK OF NEURONS
        rates[n + 1] = outfluxes[n + 1] if not FS else r_N

        # NORMALIZATION OF THE PROB DENSITY DIRECTLY AFTER THE RESPECTIVE MASS HAS BEEN REINJECTED. DENSITY BECOMES DISCONTINUOUS
        if force_normalization:
            P[grid.ib] -= sqrt_dt * sqrt(outfluxes[n - n_ref] / N) * rands[n - n_ref]/grid.dV
            mass_in -= sqrt_dt * sqrt(outfluxes[n - n_ref] / N) * rands[n - n_ref]
        # TRACKING MASS OUTSIDE THE DOMAIN
        #TODO: computation of mass_out for sure not correct
        mass_out += (outfluxes[n + 1] * dt) if not FS else r_N * sqrt_dt * dt

        # CALCULATE VMEAN (DISREGARDING THE PROBABILITY MASS THAT IS REFRACTORY)
        P_marg = P / mass_in
        V_mean[n] = np.sum(grid.dV_interfaces * P_marg * grid.V_centers)

        # GLOBAL MASS CONSERVATION OR SYSTEMAIC ERROR?
        if n < steps-1:
            mass_timecourse[n] = mass_out + mass_in

        #adaptation
        if have_adapt:
            wm[n+1] = wm[n] + dt_tauw * ( a[n] * (V_mean[n] - Ew) - wm[n] + b_tauw[n] * outfluxes[n])

        # update time
        T += dt
        # print mass conservation
        # print(mass_out + np.sum(grid.dV_interfaces*P))
    results = {'t':time, 'r':outfluxes*1000.,'reinjection': reinject,'r_n':rates*1000.,
               'r_d':r_d*1000, 'mu_ext_t':mu_ext, 'mu_total':mu_tot*1000.,
               'sigma_total':sigma_tot*1000., 'P':P, 'mass':mass_timecourse, 'wm':wm}
    if save_densities is not None:
        results['p_time'] = p_time
    import matplotlib.pyplot as plt
    plt.plot(V_mean)
    plt.show()
    return results
