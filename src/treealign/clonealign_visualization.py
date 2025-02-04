"""
CloneAlignVis class
"""
from Bio import Phylo
import pandas as pd
import numpy as np
from pandas.api.types import is_numeric_dtype
import simplejson as json
from .clonealign_tree_formatter import TreeFormatter


class CloneAlignVis:
    CHR_DICT = {'1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
                '9': 9, '10': 10, '11': 11, '12': 12, '13': 13, '14': 14, '15': 15,
                '16': 16, '17': 17, '18': 18, '19': 19, '20': 20, '21': 21, '22': 22, 'X': 23, 'Y': 24}

    def __init__(self, genes, tree, cnv_matrix=None, expr_matrix=None,
                 clone_assign_clone=None, clone_assign_tree=None, cnv_meta=None, expr_meta=None,
                 total_gene_count=2000, generate_sankey=True,
                 expr_cell_order=['clonealign_tree_id', 'clonealign_clone_id', 'infercnv_cluster_id', 'sample_id']):
        self.sankey = []
        self.genes = genes
        self.expr_cell_order = expr_cell_order

        self.tree = tree
        self.tree.ladderize()
        self.count = 0
        # add name for nodes if the nodes don't have name
        self.add_tree_node_name(self.tree.clade)

        self.cnv = cnv_matrix.copy()
        self.cnv_matrix = cnv_matrix
        self.expr_matrix = expr_matrix
        # rename column names
        self.clone_assign_clone = clone_assign_clone
        if self.clone_assign_clone is not None:
            self.clone_assign_clone = self.clone_assign_clone.rename(columns={'clone_id': 'clonealign_clone_id'})
        self.clone_assign_tree = clone_assign_tree
        self.clone_assign_tree = self.clone_assign_tree.rename(columns={'clone_id': 'clonealign_tree_id'})

        self.expr_meta = expr_meta
        self.cnv_meta = cnv_meta
        self.cnv_meta = self.cnv_meta.rename(columns={'clone_id': 'clonealign_clone_id'})

        self.total_gene_count = total_gene_count

        # if tree is not None, get cnv cell order df from tree. generate consensus data accordingly

        self.cnv_cells = pd.DataFrame({'cell_id': [terminal.name for terminal in tree.get_terminals() if terminal.name in self.cnv_matrix.columns]})
        
        # if we have both tree and tree-based clonealign results
        if self.clone_assign_tree is not None:
            # get clean clone assign tree results
            self.clone_assign_tree, self.pie_chart = TreeFormatter.clean_tree_based_clonealign_output(self.tree, self.clone_assign_tree)
            # get terminal nodes
            self.terminal_nodes = []
            for entry in self.pie_chart:
                if len(entry["value"]) == 1:
                    self.terminal_nodes.append(entry["name"])

            # get cnv cell assignments
            self.cnv_clone_assign = TreeFormatter.get_cnv_cell_assignments(self.clone_assign_tree, self.tree, self.cnv_cells)
        else:
            self.clone_assign_tree = None
            self.pie_chart = None
            self.cnv_clone_assign = None

        # merge all cnv meta data
        self.cnv_meta = CloneAlignVis.merge_meta(self.cnv_cells, 'left', self.cnv_meta, self.cnv_clone_assign)

        # clean up all the expr meta data
        self.expr_cells = pd.DataFrame({'cell_id': self.expr_matrix.columns.values.tolist()})
        
        # remove clones not in tree
        clones = set(self.cnv_clone_assign['clonealign_tree_id'].unique().tolist())
        self.clone_assign_tree = self.clone_assign_tree[self.clone_assign_tree['clonealign_tree_id'].isin(clones)]
        self.clone_assign_tree = self.clone_assign_tree[self.clone_assign_tree['clonealign_tree_id'].isin(self.terminal_nodes)]


        # else order cnv cells by clone_id
        self.expr_meta = CloneAlignVis.merge_meta(self.expr_cells, 'inner', expr_meta, self.clone_assign_tree, self.clone_assign_clone)

        # replace nan with empty string
        self.cnv_meta = self.cnv_meta.replace(np.nan, "", regex=True)
        self.expr_meta = self.expr_meta.replace(np.nan, "", regex=True)

        # re-order cells by EXPR_CELL_ORDER
        self.order_expr_cells(generate_sankey)
        self.expr_cells = pd.DataFrame({'cell_id': self.expr_meta['cell_id'].values.tolist()})

        # get consensus genes
        self.genes = self.get_consensus_genes()
        
        self.cnv_matrix = self.cnv_matrix.reindex(self.genes['gene'].values.tolist())
        self.expr_matrix = self.expr_matrix.reindex(self.genes['gene'].values.tolist())
        
        self.cnv_matrix = self.cnv_matrix.reindex(columns=self.cnv_meta['cell_id'].values.tolist())

        self.expr_matrix = self.expr_matrix.reindex(columns=self.expr_meta['cell_id'].values.tolist())

        # subsample the matrix to keep given number of genes
        self.subsample_genes()

        # bin float expr to discrete
        self.bin_expr_matrix()

    # compute clone-specific copy number profiles
    def compute_clone_specific_cnv(self, clone_id_name):
        clone_cnv_list = []
        clones = self.cnv_meta[clone_id_name].drop_duplicates().values

        for c in clones:
            clone_cells = self.cnv_meta.loc[self.cnv_meta[clone_id_name] == c, "cell_id"].values
            cnv_subset = self.cnv[clone_cells]
            current_mode = cnv_subset.mode(1)[0]
            clone_cnv_list.append(current_mode)

        clone_cnv_df = pd.concat(clone_cnv_list, axis=1)
        clone_cnv_df.columns = clones
        return clone_cnv_df

    def output_json(self):
        output = dict()
        if self.tree is not None:
            root = self.tree.clade

            def get_json(clade):
                js_output = {"name": clade.name, "length": clade.branch_length if clade.branch_length is not None else 1}
                if not clade.is_terminal():
                    clades = clade.clades
                    js_output["children"] = []
                    for clade in clades:
                        js_output["children"].append(get_json(clade))
                return js_output

            json_dict = get_json(root)
            output['tree'] = json_dict

        if self.pie_chart is not None:
            output['pie_chart'] = self.pie_chart

        if self.expr_meta is not None:
            output['expr_meta'] = self.expr_meta.to_dict('list')

        if self.cnv_meta is not None:
            output['cnv_meta'] = self.cnv_meta.to_dict('list')

        if self.expr_matrix is not None:
            output['expr_matrix'] = self.convert_cell_gene_matrix_to_list(self.expr_matrix)

        if self.cnv_matrix is not None:
            output['cnv_matrix'] = self.convert_cell_gene_matrix_to_list(self.cnv_matrix)

        if self.sankey is not None and len(self.sankey) > 0:
            output['sankey'] = self.sankey

        if self.terminal_nodes is not None:
            output['terminal_nodes'] = self.terminal_nodes
        return output

    @staticmethod
    def pack_into_tab_data(output_json_file, data, tab_titles=None, tab_contents=None):
        def convert(o):
            if isinstance(o, np.int64):
                return int(o)
            raise TypeError

        output = []
        for i in range(len(data)):
            tab_data = {'id': str(i), 'tabTitle': tab_titles[i], 'tabContent': tab_contents[i], 'data': data[i]}
            output.append(tab_data)
        with open(output_json_file, 'w') as f:
            output_json = json.dumps(output, separators=(',', ':'), sort_keys=False, ignore_nan=True, default=convert)
            f.write(output_json)
        return

    def add_tree_node_name(self, node):
        if node.is_terminal():
            return
        if node.name is None:
            node.name = "node_" + str(self.count)
            self.count += 1
        for child in node.clades:
            self.add_tree_node_name(child)
        return

    def bin_expr_matrix(self, n_bins=15):
        expr_array = self.expr_matrix.values.flatten()
       # construct bins
        bin_width = (np.median(expr_array) - expr_array.min()) / int(n_bins / 2)
        min_value = np.median(expr_array) - bin_width * n_bins / 2
        bins = [min_value]
        for i in range(n_bins):
            bins.append(min_value + (i + 1) * bin_width)

        bins[len(bins) - 1] = expr_array.max()
        self.expr_matrix = self.expr_matrix.apply(pd.cut, bins=bins, labels=range(n_bins))
        return

    def convert_cell_gene_matrix_to_list(self, matrix):
        matrix = matrix.astype('int32')
        matrix = matrix.transpose()
        output_list = []
        for i in list(self.genes['chr'].unique()):
            chr_dict = {'chr': i}
            chr_matrix = matrix.loc[:, (self.genes["chr"] == i).values]
            chr_matrix = chr_matrix.to_numpy()
            chr_matrix_array = []
            for array in chr_matrix:
                array_list = [number.item() for number in array]
                chr_matrix_array.append(array_list)
            chr_dict['value'] = chr_matrix_array
            output_list.append(chr_dict)
        return output_list

    @staticmethod
    def merge_meta(cell_order, how, *args):
        output = cell_order
        for arg in args:
            if arg is not None:
                output = output.merge(arg, how=how, on='cell_id')
        return output

    def subsample_genes(self):
        gene_group = int(self.genes.shape[0] / self.total_gene_count)
        select_rows = [i for i in range(self.genes.shape[0]) if i % gene_group == 1]
        self.genes = self.genes.iloc[select_rows]
        self.cnv_matrix = self.cnv_matrix.iloc[select_rows]
        self.expr_matrix = self.expr_matrix.iloc[select_rows]

    def order_chromosome(self, input_chr_series):
        if is_numeric_dtype(input_chr_series):
            return input_chr_series
        else:
            return input_chr_series.replace(self.CHR_DICT)

    def get_consensus_genes(self):
        genes_list = []
        if self.cnv_matrix is not None:
            cnv_genes = pd.DataFrame({'gene': self.cnv_matrix.index.values.tolist()})
            genes_list.append(cnv_genes)
        if self.expr_matrix is not None:
            expr_genes = pd.DataFrame({'gene': self.expr_matrix.index.values.tolist()})
            genes_list.append(expr_genes)
        output = self.genes
        for i in range(len(genes_list)):
            output = output.merge(genes_list[i], on='gene')
        # order genes by chromosome locations
        output = output.sort_values(by=['chr', 'start'], key=self.order_chromosome, ignore_index=True)
        return output

    def order_expr_cells(self, generateSankey=True):
        order_columns = [order_column for order_column in self.expr_cell_order if
                         order_column in self.expr_meta.columns.values]
        # if the first column is also present in self.cnv_meta, match up with self.cnv_meta
        categories = [i for i in self.cnv_meta[order_columns[0]].unique().tolist() if i is not None]
        for category in self.expr_meta[order_columns[0]].unique().tolist():
            if category is not None and category not in categories:
                categories.append(category)
        if generateSankey and order_columns[0] in self.cnv_meta.columns.values:
            self.cnv_meta[order_columns[0]] = pd.Categorical(self.cnv_meta[order_columns[0]], categories, ordered=True)
            self.expr_meta[order_columns[0]] = pd.Categorical(self.expr_meta[order_columns[0]], categories,
                                                              ordered=True)
        self.expr_meta = self.expr_meta.sort_values(by=order_columns, ignore_index=True)

        if generateSankey:
            self.generate_sankey(order_columns[0])
        return

    def generate_sankey(self, select_column):
        for terminal in self.terminal_nodes:
            left_indices = self.cnv_meta.index[self.cnv_meta[select_column] == terminal].values
            right_indices = self.expr_meta.index[self.expr_meta[select_column] == terminal].values
            if len(left_indices) != 0 and len(right_indices) != 0:
                sankey_element = {"name": terminal,
                                "left": [left_indices.min().item(), left_indices.max().item()],
                                "right": [right_indices.min().item(), right_indices.max().item()]}
                self.sankey.append(sankey_element)



