# fokker-planck-based-spike-rate-models

## Note: the code is currently in beta state and a final release is expected in November 2016.

Implementations of spike rate models derived from networks of adaptive exponential integrate-and-fire models:
* numerical solution of the mean-field Fokker-Planck (FP) equation using a finite volume method with Scharfetter-Gummel flux approximation, 
* low-dimensional ordinary differential equations (ODE) derived from the spectral decomposition of the FP operator, 
* low-dim. ODE obtained through a linear-nonlinear cascade that is fitted to FP reponse properties. 
Furthermore: precalculation codes for the quantities involved in (ii) and (iii).
References: Augustin, Ladenbauer, Baumann, Obermayer (submitted)