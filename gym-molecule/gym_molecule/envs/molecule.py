import gym
# from gym import error, spaces, utils
# from gym.utils import seeding
import numpy as np
from rdkit import Chem
from rdkit.Chem.Descriptors import qed
from rdkit.Chem import rdMolDescriptors
# import gym_molecule
import copy
import networkx as nx
from gym_molecule.envs.sascorer import calculateScore
from gym_molecule.dataset.dataset_utils import gdb_dataset,mol_to_nx,nx_to_mol
import random

from contextlib import contextmanager
import sys, os

@contextmanager
def nostdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

# TODO(Bowen): check
def convert_radical_electrons_to_hydrogens(mol):
    """
    Converts radical electrons in a molecule into bonds to hydrogens. Only
    use this if molecule is valid. Results a new mol object
    :param mol: rdkit mol object
    :return: rdkit mol object
    """
    m = copy.deepcopy(mol)
    if Chem.Descriptors.NumRadicalElectrons(m) == 0:  # not a radical
        return
    else:  # a radical
        for a in m.GetAtoms():
            num_radical_e = a.GetNumRadicalElectrons()
            if num_radical_e > 0:
                a.SetNumRadicalElectrons(0)
                a.SetNumExplicitHs(num_radical_e)
    return m

class MoleculeEnv(gym.Env):
    metadata = {'render.modes': ['human']}
    # todo: seed()

    def __init__(self):
        # possible_atoms = ['C', 'N', 'O', 'S', 'Cl'] # gdb 13
        possible_atoms = ['B', 'C', 'N', 'O', 'S', 'P', 'F', 'I', 'Cl',
                          'Br']  # ZINC
        possible_bonds = [Chem.rdchem.BondType.SINGLE, Chem.rdchem.BondType.DOUBLE,
                          Chem.rdchem.BondType.TRIPLE] #, Chem.rdchem.BondType.AROMATIC
        self.mol = Chem.RWMol()
        self.possible_atom_types = np.array(possible_atoms)  # dim d_n. Array that
        self.atom_type_num = len(possible_atoms)
        # contains the possible atom symbols strs
        self.possible_bond_types = np.array(possible_bonds, dtype=object)  # dim
        # d_e. Array that contains the possible rdkit.Chem.rdchem.BondType objects

        # self.max_atom = 13 + len(possible_atoms) # gdb 13
        self.max_atom = 38 + len(possible_atoms) # ZINC
        self.max_action = 200
        self.logp_ratio = 5
        self.qed_ratio = 1
        self.sa_ratio = -0.1
        self.action_space = gym.spaces.MultiDiscrete([self.max_atom, self.max_atom, 3])
        self.observation_space = {}
        self.observation_space['adj'] = gym.Space(shape=[len(possible_bonds), self.max_atom, self.max_atom])
        self.observation_space['node'] = gym.Space(shape=[1, self.max_atom, len(possible_atoms)])

        self.counter = 0

        ## load expert data
        cwd = os.path.dirname(__file__)
        path = os.path.join(os.path.dirname(cwd), 'dataset',
                            'gdb13.rand1M.smi.gz')  # gdb 13
        # path = os.path.join(os.path.dirname(cwd), 'dataset',
        #                     '250k_rndm_zinc_drugs_clean.smi')  # ZINC
        self.dataset = gdb_dataset(path)



    def step(self, action):
        """
            Perform a given action
            :param action:
            :param action_type:
            :return: reward of 1 if resulting molecule graph does not exceed valency,
            -1 if otherwise
            """
        self.mol_old = copy.deepcopy(self.mol)
        # print('num atoms',self.mol.GetNumAtoms())
        total_atoms = self.mol.GetNumAtoms()
        # take action

        if action[0, 1] >= total_atoms:
            self._add_atom(action[0, 1] - total_atoms)  # add new node
            action[0, 1] = total_atoms  # new node id
            self._add_bond(action)  # add new edge
        else:
            self._add_bond(action)  # add new edge

        reward = 0
        # calculate intermediate rewards
        if self.check_valency():
            if self.mol.GetNumAtoms()+self.mol.GetNumBonds()-self.mol_old.GetNumAtoms()-self.mol_old.GetNumBonds()>0:
                reward_step = 1/self.max_atom
            else:
                reward_step = -1/self.max_atom
        else:
            reward_step = -1/self.max_atom  # arbitrary choice
            self.mol = self.mol_old

        # calculate terminal rewards
        if self.mol.GetNumAtoms() >= self.max_atom-self.possible_atom_types.shape[0] or self.counter >= self.max_action:
            #  some arbitrary termination condition for episode

            # check chemical validity of final molecule (valency, as well as
            # other rdkit molecule checks, such as aromaticity)
            if not self.check_chemical_validity():
                reward_valid = -10 # arbitrary choice
                reward_qed = 0
                reward_logp = 0
                reward_sa = 0
                # reward_cycle = 0
            else:   # these metrics only work for valid molecules
                # drug likeness metric to optimize. qed can have values [0, 1]
                reward_valid = 1
                try:
                    # reward_qed = 5**qed(self.mol)    # arbitrary choice of exponent
                    reward_qed = qed(self.mol)
                # log p. Assume we want to increase log p. log p typically
                # have values between -3 and 7
                    reward_logp = Chem.Crippen.MolLogP(self.mol)/self.mol.GetNumAtoms()    # arbitrary choice
                    s = Chem.MolToSmiles(self.mol, isomericSmiles=True)
                    m = Chem.MolFromSmiles(s)  # implicitly performs sanitization
                    reward_sa = calculateScore(m) # lower better

                    # cycle_list = nx.cycle_basis(nx.Graph(Chem.GetAdjacencyMatrix(self.mol)))
                    # if len(cycle_list) == 0:
                    #     cycle_length = 0
                    # else:
                    #     cycle_length = max([len(j) for j in cycle_list])
                    # if cycle_length <= 6:
                    #     cycle_length = 0
                    # else:
                    #     cycle_length = cycle_length - 6
                    # reward_cycle = cycle_length

                    # if self.mol.GetNumAtoms() >= self.max_atom-self.possible_atom_types.shape[0]:
                    #     reward_sa = calculateScore(self.mol)
                    # else:
                    #     reward_sa = 0
                except:
                    reward_qed = -1
                    reward_logp = -1
                    reward_sa = 10
                    print('error')

                #TODO(Bowen): synthetic accessibility metric to optimize
            new = True # end of episode
            # reward = reward_step + reward_valid + reward_logp +reward_qed*self.qed_ratio
            # reward = reward_step + reward_valid + reward_logp - reward_sa - reward_cycle
            # reward = reward_step + reward_valid + reward_logp + reward_qed*self.qed_ratio - reward_sa*self.sa_ratio
            reward = reward_step + reward_valid + reward_qed*self.qed_ratio + reward_logp*self.logp_ratio + reward_sa*self.sa_ratio
            smile = Chem.MolToSmiles(self.mol, isomericSmiles=True)
            # print('counter', self.counter, 'new', new, 'reward', reward)
            # print('reward_valid', reward_valid, 'reward_qed', reward_qed*self.qed_ratio, 'reward_logp', reward_logp*self.logp_ratio, 'reward_sa', reward_sa*self.sa_ratio, 'qed_ratio', self.qed_ratio,'logp_ratio', self.logp_ratio, 'sa_ratio', self.sa_ratio)
            # print('smile',smile)
        else:
            new = False
            # print('counter', self.counter, 'new', new, 'reward_step', reward_step)
            reward = reward_step


        # get observation
        ob = self.get_observation()

        info = {} # info we care about

        self.counter += 1
        if new:
            self.counter = 0

        return ob,reward,new,info


    def reset(self):
        '''
        to avoid error, assume an atom already exists
        :return: ob
        '''
        self.mol = Chem.RWMol()
        self._add_atom(np.random.randint(len(self.possible_atom_types)))  # random add one atom
        self.counter = 0
        ob = self.get_observation()
        return ob

    def render(self, mode='human', close=False):
        return

    def _add_atom(self, atom_type_id):
        """
        Adds an atom
        :param atom_type_id: atom_type id
        :return:
        """
        # assert action.shape == (len(self.possible_atom_types),)
        # atom_type_idx = np.argmax(action)
        atom_symbol = self.possible_atom_types[atom_type_id]
        self.mol.AddAtom(Chem.Atom(atom_symbol))

    def _add_bond(self, action):
        '''

        :param action: [first_node, second_node, bong_type_id]
        :return:
        '''
        # GetBondBetweenAtoms fails for np.int64
        bond_type = self.possible_bond_types[action[0,2]]

        # if bond exists between current atom and other atom, modify the bond
        # type to new bond type. Otherwise create bond between current atom and
        # other atom with the new bond type
        bond = self.mol.GetBondBetweenAtoms(int(action[0,0]), int(action[0,1]))
        if bond:
            # print('bond exist!')
            return False
        else:
            self.mol.AddBond(int(action[0,0]), int(action[0,1]), order=bond_type)
            return True

    def _modify_bond(self, action):
        """
        Adds or modifies a bond (currently no deletion is allowed)
        :param action: np array of dim N-1 x d_e, where N is the current total
        number of atoms, d_e is the number of bond types
        :return:
        """
        assert action.shape == (self.current_atom_idx, len(self.possible_bond_types))
        other_atom_idx = int(np.argmax(action.sum(axis=1)))  # b/c
        # GetBondBetweenAtoms fails for np.int64
        bond_type_idx = np.argmax(action.sum(axis=0))
        bond_type = self.possible_bond_types[bond_type_idx]

        # if bond exists between current atom and other atom, modify the bond
        # type to new bond type. Otherwise create bond between current atom and
        # other atom with the new bond type
        bond = self.mol.GetBondBetweenAtoms(self.current_atom_idx, other_atom_idx)
        if bond:
            bond.SetBondType(bond_type)
        else:
            self.mol.AddBond(self.current_atom_idx, other_atom_idx, order=bond_type)
            self.total_bonds += 1

    def get_num_atoms(self):
        return self.total_atoms

    def get_num_bonds(self):
        return self.total_bonds

    def check_chemical_validity(self):
        """
        Checks the chemical validity of the mol object. Existing mol object is
        not modified. Radicals pass this test.
        :return: True if chemically valid, False otherwise
        """
        s = Chem.MolToSmiles(self.mol, isomericSmiles=True)
        m = Chem.MolFromSmiles(s)  # implicitly performs sanitization
        if m:
            return True
        else:
            return False

    def check_valency(self):
        """
        Checks that no atoms in the mol have exceeded their possible
        valency
        :return: True if no valency issues, False otherwise
        """
        try:
            Chem.SanitizeMol(self.mol,
                             sanitizeOps=Chem.SanitizeFlags.SANITIZE_PROPERTIES)
            return True
        except ValueError:
            return False

    # TODO(Bowen): check
    def get_final_smiles(self):
        """
        Returns a SMILES of the final molecule. Converts any radical
        electrons into hydrogens. Works only if molecule is valid
        :return: SMILES
        """
        m = convert_radical_electrons_to_hydrogens(self.mol)
        return Chem.MolToSmiles(m, isomericSmiles=True)

    # TODO(Bowen): check
    def get_final_mol(self):
        """
        Returns a rdkit mol object of the final molecule. Converts any radical
        electrons into hydrogens. Works only if molecule is valid
        :return: SMILES
        """
        m = convert_radical_electrons_to_hydrogens(self.mol)
        return m

    def get_info(self):
        info = {}
        info['smile'] = Chem.MolToSmiles(self.mol, isomericSmiles=True)
        info['reward_qed'] = qed(self.mol) * self.qed_ratio
        info['reward_logp'] = Chem.Crippen.MolLogP(self.mol) / self.mol.GetNumAtoms() * self.logp_ratio  # arbitrary choice
        s = Chem.MolToSmiles(self.mol, isomericSmiles=True)
        m = Chem.MolFromSmiles(s)  # implicitly performs sanitization
        info['reward_sa'] = calculateScore(m) * self.sa_ratio  # lower better
        info['reward_sum'] = info['reward_qed'] + info['reward_logp'] + info['reward_sa']
        info['qed_ratio'] = self.qed_ratio
        info['logp_ratio'] = self.logp_ratio
        info['sa_ratio'] = self.sa_ratio
        return info

    def get_matrices(self):
        """
        Get the adjacency matrix, edge feature matrix and, node feature matrix
        of the current molecule graph
        :return: np arrays: adjacency matrix, dim n x n; edge feature matrix,
        dim n x n x d_e; node feature matrix, dim n x d_n
        """
        A_no_diag = Chem.GetAdjacencyMatrix(self.mol)
        A = A_no_diag + np.eye(*A_no_diag.shape)

        n = A.shape[0]

        d_n = len(self.possible_atom_types)
        F = np.zeros((n, d_n))
        for a in self.mol.GetAtoms():
            atom_idx = a.GetIdx()
            atom_symbol = a.GetSymbol()
            float_array = (atom_symbol == self.possible_atom_types).astype(float)
            assert float_array.sum() != 0
            F[atom_idx, :] = float_array

        d_e = len(self.possible_bond_types)
        E = np.zeros((n, n, d_e))
        for b in self.mol.GetBonds():
            begin_idx = b.GetBeginAtomIdx()
            end_idx = b.GetEndAtomIdx()
            bond_type = b.GetBondType()
            float_array = (bond_type == self.possible_bond_types).astype(float)
            assert float_array.sum() != 0
            E[begin_idx, end_idx, :] = float_array
            E[end_idx, begin_idx, :] = float_array

        return A, E, F

    def get_observation(self):
        """
        ob['adj']:b*n*n --- 'E'
        ob['node']:1*n*m --- 'F'
        n = atom_num + atom_type_num
        """

        n = self.mol.GetNumAtoms()
        n_shift = len(self.possible_atom_types) # assume isolated nodes new nodes exist

        d_n = len(self.possible_atom_types)
        F = np.zeros((1, self.max_atom, d_n))
        for a in self.mol.GetAtoms():
            atom_idx = a.GetIdx()
            atom_symbol = a.GetSymbol()
            float_array = (atom_symbol == self.possible_atom_types).astype(float)
            assert float_array.sum() != 0
            F[0, atom_idx, :] = float_array
        temp = F[0,n:n+n_shift,:]
        F[0,n:n+n_shift,:] = np.eye(n_shift)

        d_e = len(self.possible_bond_types)
        E = np.zeros((d_e, self.max_atom, self.max_atom))
        for i in range(d_e):
            E[i,:n+n_shift,:n+n_shift] = np.eye(n+n_shift)
        for b in self.mol.GetBonds():
            begin_idx = b.GetBeginAtomIdx()
            end_idx = b.GetEndAtomIdx()
            bond_type = b.GetBondType()
            float_array = (bond_type == self.possible_bond_types).astype(float)
            assert float_array.sum() != 0
            E[:, begin_idx, end_idx] = float_array
            E[:, end_idx, begin_idx] = float_array
        ob = {}
        ob['adj'] = E
        ob['node'] = F
        return ob


    def get_expert(self, batch_size):
        ob = {}
        atom_type_num = len(self.possible_atom_types)
        bond_type_num = len(self.possible_bond_types)
        ob['node'] = np.zeros((batch_size, 1, self.max_atom, atom_type_num))
        ob['adj'] = np.zeros((batch_size, bond_type_num, self.max_atom, self.max_atom))

        ac = np.zeros((batch_size, 3))
        ### select molecule
        dataset_len = len(self.dataset)
        np.random.randint(0,dataset_len,size=batch_size)
        for i in range(batch_size):
            ### get a subgraph
            mol = self.dataset[i]
            Chem.SanitizeMol(mol,sanitizeOps=Chem.SanitizeFlags.SANITIZE_KEKULIZE)
            graph = mol_to_nx(mol)
            edges = graph.edges()
            edges_sub_len = random.randint(1,len(edges))
            edges_sub = random.sample(edges,k=edges_sub_len)
            graph_sub = nx.Graph(edges_sub)
            graph_sub = max(nx.connected_component_subgraphs(graph_sub), key=len)
            ### random pick an edge from the subgraph, then remove it
            edge_sample = random.sample(graph_sub.edges(),k=1)
            graph_sub.remove_edges_from(edge_sample)
            graph_sub = max(nx.connected_component_subgraphs(graph_sub), key=len)
            edge_sample = edge_sample[0] # get value
            ### get action
            graph_sub_nodes = graph_sub.nodes()
            if edge_sample[0] in graph_sub_nodes and edge_sample[1] in graph_sub_nodes:
                node1 = graph_sub_nodes.index(edge_sample[0])
                node2 = graph_sub_nodes.index(edge_sample[1])
            elif edge_sample[0] in graph_sub_nodes:
                node1 = graph_sub_nodes.index(edge_sample[0])
                node2 = np.argmax(
                    graph.node[edge_sample[1]]['symbol'] == self.possible_atom_types) + graph_sub.number_of_nodes()
            elif edge_sample[1] in graph_sub_nodes:
                node1 = graph_sub_nodes.index(edge_sample[1])
                node2 = np.argmax(
                    graph.node[edge_sample[0]]['symbol'] == self.possible_atom_types) + graph_sub.number_of_nodes()
            else:
                print('Expert policy error!')
            edge_type = np.argmax(graph[edge_sample[0]][edge_sample[1]]['bond_type'] == self.possible_bond_types)
            ac[i,:] = [node1,node2,edge_type]

            ### get observation
            n = graph_sub.number_of_nodes()
            for node_id, node in enumerate(graph_sub_nodes):
                float_array = (graph.node[node]['symbol'] == self.possible_atom_types).astype(float)
                assert float_array.sum() != 0
                ob['node'][i, 0, node_id, :] = float_array
            ob['node'][i ,0, n:n + atom_type_num, :] = np.eye(atom_type_num)

            for j in range(bond_type_num):
                ob['adj'][i, j, :n + atom_type_num, :n + atom_type_num] = np.eye(n + atom_type_num)
            for edge in graph_sub.edges():
                begin_idx = edge[0]
                end_idx = edge[1]
                bond_type = graph[begin_idx][end_idx]['bond_type']
                float_array = (bond_type == self.possible_bond_types).astype(float)
                assert float_array.sum() != 0
                ob['adj'][i, :, begin_idx, end_idx] = float_array
                ob['adj'][i, :, end_idx, begin_idx] = float_array

        return ob,ac

### YES/NO filters ###

from rdkit.Chem.FilterCatalog import FilterCatalogParams, FilterCatalog
class zinc_molecule_filter:
    """
    Flags molecules based on problematic functional groups as
    provided set of ZINC rules from
    http://blaster.docking.org/filtering/rules_default.txt
    """
    def __init__(self):
        params = FilterCatalogParams()
        params.AddCatalog(FilterCatalogParams.FilterCatalogs.ZINC)
        self.catalog = FilterCatalog(params)

    def __call__(self, mol):
        return self.check_molecule_pass(mol)

    def check_molecule_pass(self, mol):
        """
        Returns True if molecule is okay (ie does not match any of the
        rules), False if otherwise
        :param mol: rdkit mol object
        :return:
        """
        return not self.catalog.HasMatch(mol)

# TODO(Bowen): check
from rdkit.Chem import AllChem
def steric_strain_filter(mol, forcefield='uff', cutoff=320,
                         max_attempts_embed=20,
                         max_num_iters=200):
    """
    Flags molecules based on a steric energy cutoff after max_num_iters
    iterations of UFF forcefield minimization
    :param mol: rdkit mol object
    :param forcefield: forcefield type. either uff or mmff94
    :param cutoff: kcal/mol units. If minimized energy is above this
    threshold, then molecule fails the steric strain filter
    :param max_attempts_embed: number of attempts to generate initial 3d
    coordinates
    :param max_num_iters: number of iterations of forcefield minimization
    :return: True if molecule could be successfully minimized, and resulting
    energy is below cutoff, otherwise False
    """
    # make copy of input mol and add hydrogens
    m = copy.deepcopy(mol)
    Chem.SanitizeMol(m) # TODO(Bowen): check if this is even necessary?
    m_h = Chem.AddHs(m)

    # generate an initial 3d conformer
    flag = AllChem.EmbedMolecule(m_h, maxAttempts=max_attempts_embed)
    if flag == -1:
        print("Unable to generate 3d conformer")
        return False

    # set up the forcefield
    if forcefield == 'mmff94':
        AllChem.MMFFSanitizeMolecule(m_h)
        if AllChem.MMFFHasAllMoleculeParams(m_h):
            mmff_props = AllChem.MMFFGetMoleculeProperties(m_h)
            ff = AllChem.MMFFGetMoleculeForceField(m_h, mmff_props)
        else:
            print("Unrecognized atom type")
            return False
    elif forcefield == 'uff':
        if AllChem.UFFHasAllMoleculeParams(m_h):
            ff = AllChem.UFFGetMoleculeForceField(m_h)
        else:
            print("Unrecognized atom type")
            return False
    else:
        return ValueError("Invalid forcefield type")

    # calculate steric energy
    try:
        ff.Minimize(maxIts=max_num_iters)
    except:
        print("Minimization error")
        return False

    min_e = ff.CalcEnergy()
    print("Minimized energy: {}".format(min_e))

    if min_e < cutoff:
        return True
    else:
        return False

### TARGET VALUE REWARDS ###

def reward_target_log_p(mol, target):
    """
    Reward for a target log p
    :param mol: rdkit mol object
    :param target: float
    :return: float (-inf, 1]
    """
    x = Chem.Crippen.MolLogP(mol)
    reward = -1 * (x - target)**2 + 1
    return reward

def reward_target_mw(mol, target):
    """
    Reward for a target molecular weight
    :param mol: rdkit mol object
    :param target: float
    :return: float (-inf, 1]
    """
    x = rdMolDescriptors.CalcExactMolWt(mol)
    reward = -1 * (x - target)**2 + 1
    return reward

# TODO(Bowen): num rings is a discrete variable, so what is the best way to
# calculate the reward?
def reward_target_num_rings(mol, target):
    """
    Reward for a target number of rings
    :param mol: rdkit mol object
    :param target: int
    :return: float (-inf, 1]
    """
    x = rdMolDescriptors.CalcNumRings(mol)
    reward = -1 * (x - target)**2 + 1
    return reward

# TODO(Bowen): more efficient if we precalculate the target fingerprint
from rdkit import DataStructs
def reward_target_molecule_similarity(mol, target, radius=2, nBits=2048,
                                      useChirality=True):
    """
    Reward for a target molecule similarity, based on tanimoto similarity
    between the ECFP fingerprints of the x molecule and target molecule
    :param mol: rdkit mol object
    :param target: rdkit mol object
    :return: float, [0.0, 1.0]
    """
    x = rdMolDescriptors.GetMorganFingerprintAsBitVect(mol, radius=radius,
                                                        nBits=nBits,
                                                        useChirality=useChirality)
    target = rdMolDescriptors.GetMorganFingerprintAsBitVect(target,
                                                            radius=radius,
                                                        nBits=nBits,
                                                        useChirality=useChirality)
    return DataStructs.TanimotoSimilarity(x, target)


if __name__ == '__main__':
    env = gym.make('molecule-v0') # in gym format

    ob = env.reset()
    print(ob['adj'].shape)
    print(ob['node'].shape)

    ob,ac = env.get_expert(4)
    print(ob)
    print(ac)

    # env.step(np.array([[0,3,0]]))
    # env.step(np.array([[1,4,0]]))
    # env.step(np.array([[0,4,0]]))
    # env.step(np.array([[0,4,0]]))
    #
    # s = Chem.MolToSmiles(env.mol, isomericSmiles=True)
    # m = Chem.MolFromSmiles(s)  # implicitly performs sanitization
    #
    # ring = m.GetRingInfo()
    # print('ring',ring.NumAtomRings(0))
    # s = calculateScore(m)
    # print(s)

    # ac = np.array([[0,3,0]])
    # print(ac.shape)
    # ob,reward,done,info = env.step([[0,3,0]])
    # ob, reward, done, info = env.step([[0, 4, 0]])
    # print('after add node')
    # print(ob['adj'])
    # print(ob['node'])
    # print(reward)


    # # add carbon
    # env.step(np.array([1, 0, 0]), 'add_atom')
    # # add carbon
    # env.step(np.array([1, 0, 0]), 'add_atom')
    # # add double bond between carbon 1 and carbon 2
    # env.step(np.array([[0, 1, 0]]), 'modify_bond')
    # # add carbon
    # env.step(np.array([1, 0, 0]), 'add_atom')
    # # add single bond between carbon 2 and carbon 3
    # env.step(np.array([[0, 0, 0], [1, 0, 0]]), 'modify_bond')
    # # add oxygen
    # env.step(np.array([0, 0, 1]), 'add_atom')
    #
    # # add single bond between carbon 3 and oxygen
    # env.step(np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0]]), 'modify_bond')

    # A,E,F = env.get_matrices()
    # print('A',A.shape)
    # print('E',E.shape)
    # print('F',F.shape)
    # print('A\n',A)
    # print('E\n', np.sum(E,axis=2))

    # print(env.check_valency())
    # print(env.check_chemical_validity())

    # # test get ob
    # ob = env.get_observation()
    # print(ob['adj'].shape,ob['node'].shape)
    # for i in range(3):
    #     print(ob['adj'][i])
    # print(ob['node'])