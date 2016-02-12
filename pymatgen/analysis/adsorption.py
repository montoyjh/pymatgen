# coding: utf-8
# Copyright (c) Pymatgen Development Team.
# Distributed under the terms of the MIT License.

from __future__ import division, unicode_literals
from __future__ import absolute_import

"""
This module provides classes used to enumerate surface sites
and to find adsorption sites on slabs
"""

import numpy as np
from six.moves import range
from pymatgen.core.structure import Structure
import subprocess
import itertools
from pyhull.delaunay import DelaunayTri
from pyhull.voronoi import VoronoiTess
from pymatgen.core.operations import SymmOp
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.symmetry.analyzer import generate_full_symmops
from pymatgen.util.coord_utils import in_coord_list, in_coord_list_pbc
from pymatgen.core.sites import PeriodicSite

__author__ = "Joseph Montoya"
__copyright__ = "Copyright 2015, The Materials Project"
__version__ = "1.0"
__maintainer__ = "Joseph Montoya"
__email__ = "montoyjh@lbl.gov"
__status__ = "Development"
__date__ = "December 2, 2015"


class AdsorbateSiteFinder(object):
    """
    This class finds adsorbate sites on slabs
    """

    def __init__(self, slab, selective_dynamics = False):
        """
        Create an AdsorbateSiteFinder object.

        Args:
            slab (Slab): slab object for which to find adsorbate
            sites
        """
        slab = self.assign_site_properties(slab)
        if selective_dynamics:
            slab = self.assign_selective_dynamics(slab)

        self.slab = reorient_z(slab)

    def find_surface_sites_by_height(self, slab, window = 1.0):
        """
        This method finds surface sites by determining which sites are within
        a threshold value in height from the topmost site in a list of sites

        Args:
            site_list (list): list of sites from which to select surface sites
            window (float): threshold in angstroms of distance from topmost
                site in slab along the slab c-vector to include in surface 
                site determination

        Returns:
            list of sites selected to be within a threshold of the highest
        """

        # Determine the window threshold in fractional coordinates
        c_window = window / np.linalg.norm(slab.lattice.matrix[-1])
        highest_site_z = max([site.frac_coords[-1] for site in slab.sites])

        return [site for site in slab.sites 
                if site.frac_coords[-1] >= highest_site_z - c_window]

    def assign_site_properties(self, slab):
        surf_sites = self.find_surface_sites_by_height(slab)
        return slab.copy(site_properties = {'surface_properties': ['surface' if site in surf_sites
                                                           else 'subsurface' for site in 
                                                           slab.sites]})

    def find_surface_sites_by_alpha(self, slab, alpha = None):
        """
        This method finds surface sites by determining which sites are on the
        top layer of an alpha shape corresponding to the slab repeated once
        in each direction

        Args:
            site_list (list): list of sites from which to select surface sites
            alpha (float): alpha value criteria for creating alpha shape 
                for the slab object
        """
        # construct a mesh from slab repeated three times
        frac_coords = np.array([site.frac_coords for site in slab.sites])
        repeated = np.array([i + (0,) for i in 
                             itertools.product([-1,0,1], repeat=2)])
        mesh = [r + fc for r, fc in itertools.product(repeated,
                                                      frac_coords)]
        # convert mesh to input string for Clarkson hull
        mesh_string = '\n'.join([' '.join([str(j) for j in frac_coords]) 
                                 for frac_coords in mesh])
        ahull_string = subprocess.check_output(["hull", "-A"], stdin = mesh_string)

    def get_extended_surface_mesh(self, radius = 4.0, window = 1.0):
        """
        """
        surf_str = Structure.from_sites(self.surface_sites)
        surface_mesh = []
        for site in surf_str.sites:
            surface_mesh += [site]
            surface_mesh += [s[0] for s in surf_str.get_neighbors(site,
                                                                  radius)]
        return list(set(surface_mesh))

    @property
    def surface_sites(self):
        """
        convenience method to return a list of surface sites
        """
        return [site for site in self.slab.sites 
                if site.properties['surface_properties']=='surface']

    def find_adsorption_sites(self, distance = 2.0, put_inside = True,
                              symm_reduce = True, near_reduce = True,
                              near_reduce_threshold = 1e-3):
        """
        """
        # Find vector for distance normal to x-y plane
        # TODO: check redundancy since slabs are reoriented now
        a, b, c = self.slab.lattice.matrix
        dist_vec = np.cross(a, b)
        dist_vec = distance * dist_vec / np.linalg.norm(dist_vec)
        if np.dot(dist_vec, c) < 0:
            dist_vec = -dist_vec
        # find on-top sites
        ads_sites = [s.coords for s in self.surface_sites]
        # Get bridge sites via DelaunayTri of extended surface mesh
        mesh = self.get_extended_surface_mesh()
        dt = DelaunayTri([m.coords[:2] for m in mesh])
        for v in dt.vertices:
            # Add bridge sites at edges of delaunay
            if -1 not in v:
                for data in itertools.combinations(v, 2):
                    ads_sites += [self.ensemble_center(mesh, data, 
                                                       cartesian = True)]
            # Add hollow sites at centers of delaunay
                ads_sites += [self.ensemble_center(mesh, v, cartesian = True)]
        if put_inside:
            ads_sites = [put_coord_inside(self.slab.lattice, coord) 
                         for coord in ads_sites]
        if near_reduce:
            ads_sites = self.near_reduce(ads_sites, 
                                         threshold=near_reduce_threshold)
        if symm_reduce:
            ads_sites = self.symm_reduce(ads_sites)
        ads_sites = [ads_site + dist_vec for ads_site in ads_sites]
        return ads_sites

    def symm_reduce(self, coords_set, cartesian = True,
                    threshold = 0.1, mrd = 200):
        """
        """
        surf_sg = SpacegroupAnalyzer(self.slab, 0.1)
        symm_ops = surf_sg.get_symmetry_operations(cartesian = cartesian)
        full_symm_ops = generate_full_symmops(symm_ops, tol=0.1, max_recursion_depth=mrd)
        unique_coords = []
        # coords_set = [[coord[0], coord[1], 0] for coord in coords_set]
        for coords in coords_set:
            incoord = False
            for op in full_symm_ops:
                if in_coord_list(unique_coords, op.operate(coords)):
                    incoord = True
                    break
            if not incoord:
                unique_coords += [coords]
        return unique_coords

    def near_reduce(self, coords_set, threshold = 1e-3, pbc = True):
        """
        Prunes coordinate set for coordinates that are within a certain threshold
        
        Args:
            coords_set (Nx3 array-like): list or array of coordinates
            threshold (float): threshold value for distance
        """
        unique_coords = []
        for coord in coords_set:
            if not in_coord_list(unique_coords, coord, threshold):
                unique_coords += [coord]
        return unique_coords

    def ensemble_center(self, site_list, indices, cartesian = True):
        """
        """
        if cartesian:
            return np.average([site_list[i].coords for i in indices], 
                              axis = 0)
        else:
            return np.average([site_list[i].frac_coords for i in indices], 
                              axis = 0)

    def add_adsorbate(self, ads_atom_list, ads_position_list, ads_coord, 
                      repeat = None):
        """
        Adds an adsorbate at a particular coordinate
        """
        struct = self.slab.copy()
        if repeat:
            struct.make_supercell(repeat)
        ads_position_list = np.array(ads_position_list)
        ads_coord = np.array(ads_coord)
        for atom, position in zip(ads_atom_list, ads_position_list):
            struct.append(atom, ads_coord + position, coords_are_cartesian = True)
            site_props = {}
            if 'surface_properties' in struct.site_properties.keys():
                site_props['surface_properties'] = struct.site_properties['surface_properties'][:-1] + ['adsorbate']
                struct = struct.copy(site_properties=site_props)
            if 'selective_dynamics' in struct.site_properties.keys():
                struct = self.assign_selective_dynamics(struct)
        return struct
        
    def assign_selective_dynamics(self, slab):
        sd_list = []
        sd_list = [['False', 'False', 'False']  if site.properties['surface_properties']=='subsurface' 
                   else ['True', 'True', 'True'] for site in slab.sites]
        new_sp = slab.site_properties
        new_sp['selective_dynamics'] = sd_list
        return slab.copy(site_properties = new_sp)

    def generate_adsorption_structures(self, adsorbate, ads_position_list,
                                       repeat = None, min_size = 5.0):
        if repeat is None:
            xrep = np.ceil(min_size / np.linalg.norm(self.slab.lattice.matrix[0]))
            yrep = np.ceil(min_size / np.linalg.norm(self.slab.lattice.matrix[1]))
            repeat = [xrep, yrep, 1]
        structs = []
        for coords in self.find_adsorption_sites():
            structs += [self.add_adsorbate(adsorbate, ads_position_list, coords,
                                      repeat = repeat)]
        return structs

def reorient_z(structure):
    """
    reorients a structure such that the z axis is concurrent with the 
    normal to the A-B plane
    """
    struct = structure.copy()
    a, b, c = structure.lattice.matrix
    new_x = a / np.linalg.norm(a)
    new_y = (b - np.dot(new_x, b) * new_x) / \
            np.linalg.norm(b - np.dot(new_x, b) * new_x)
    new_z = np.cross(new_x, new_y)
    if np.dot(new_z, c) < 0.:
        new_z = -new_z
    x, y, z = np.eye(3)
    rot_matrix = np.array([np.dot(*el) for el in 
                           itertools.product([x, y, z], 
                                   [new_x, new_y, new_z])]).reshape(3,3)
    rot_matrix = np.transpose(rot_matrix)
    sop = SymmOp.from_rotation_and_translation(rot_matrix)
    struct.apply_operation(sop)
    return struct

def frac_to_cart(lattice, frac_coord):
    """
    converts fractional coordinates to cartesian
    """
    return np.dot(np.transpose(lattice.matrix), frac_coord)

def cart_to_frac(lattice, cart_coord):
    """
    converts cartesian coordinates to fractional
    """
    return np.dot(np.linalg.inv(np.transpose(lattice.matrix)), cart_coord)

def put_coord_inside(lattice, cart_coordinate):
    """
    converts a cartesian coordinate such that it is inside the unit cell.
    This assists with the symmetry and near reduction algorithms.
    """
    fc = cart_to_frac(lattice, cart_coordinate)
    return frac_to_cart(lattice, [c - np.floor(c) for c in fc])

if __name__ == "__main__":
    from pymatgen.matproj.rest import MPRester
    from pymatgen.core.surface import generate_all_slabs
    mpr = MPRester()
    struct = mpr.get_structures('mp-124')[0]
    sga = SpacegroupAnalyzer(struct, 0.1)
    struct = sga.get_conventional_standard_structure()
    slabs = generate_all_slabs(struct, 1, 5.0, 5.0, 
                               max_normal_search = 1,
                               center_slab = True)
    asf = AdsorbateSiteFinder(slabs[2], selective_dynamics = True)

    #surf_sites_height = asf.find_surface_sites_by_height(slabs[0])
    #surf_sites_alpha = asf.find_surface_sites_by_alpha(slabs[0])
    #sites = asf.find_adsorption_sites(near_reduce = False, put_inside = False)
    structs = asf.generate_adsorption_structures('O', [[0.0, 0.0, 0.0]])
                                                    #repeat = [2, 2, 1])
    '''
    from pymatgen.vis.structure_vtk import StructureVis
    sv = StructureVis()
    sv.set_structure(structs[0])
    sv.write_image()
    '''
    # from helper import pymatview
    # pymatview(structs)
