from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch
from accelerate import Accelerator
from torch.utils.data import DataLoader
from tqdm import tqdm

from smoe.data.collate_fn import fault_tolerance_data_collator
from smoe.data.streaming import CachedJsonlDataset
from smoe.models.llama_moe import LlamaMoEForCausalLM
from smoe.utils.visualization.visualize import visualize_expert_load_heatmap


@torch.no_grad()
def main(
    model_dir="/mnt/petrelfs/share_data/quxiaoye/runs/llama2_random_scale4_112gpus/outputs/cpt-llama2_random_scale4_112gpus-2220221/checkpoint-13600/",
    result_dir="/mnt/petrelfs/zhutong/smoe/results/llama2_7B_random_split_baseline_gate_load/",
):
    bsz = 4
    # model_dir = "/mnt/petrelfs/share_data/quxiaoye/models/LlamaMoEForCausalLM/Gradient-max-l1_norm-sample-feature_change/llama2_7B-16Select4-688Neurons-Share"
    # model_dir = "/mnt/petrelfs/zhutong/smoe/outputs/cpt-7b-4_16_noisygate-gate_stage1-2090437/checkpoint-4000"
    # model_dir = "/mnt/petrelfs/zhutong/smoe/outputs/cpt-7b-4_16_noisygate-gate_stage2-2105807/checkpoint-4000"
    eval_path_map = {
        "en_wikipedia": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/en_wikipedia.jsonl",
        "github": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/github.jsonl",
        "en_stack": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/en_stack.jsonl",
        "en_cc": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/en_cc.jsonl",
        "en_c4": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/en_c4.jsonl",
        "en_book": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/en_book.jsonl",
        "en_arxiv": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/en_arxiv.jsonl",
        "arc_challenge": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/arc_challenge.jsonl",
        "gsm8k": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/gsm8k.jsonl",  # 37998 tokens
        "hellaswag": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/hellaswag.jsonl",
        "mmlu": "/mnt/petrelfs/share_data/quxiaoye/data/llama1_7B_val_set_tokenized/mmlu.jsonl",  # 23720 tokens
    }
    # result_dir = "/mnt/petrelfs/zhutong/smoe/results/llama2_7B_gradient_share_gate_load/stage1_trained_more/"

    result_dir = Path(result_dir)
    result_dir.mkdir(exist_ok=True, parents=True)

    accel = Accelerator()
    model = LlamaMoEForCausalLM.from_pretrained(
        model_dir,
        torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True,
    )
    model.eval()
    model = accel.prepare_model(model)

    eval_dataset = {
        name: CachedJsonlDataset(path, seed=1227, block_size=4096)
        for name, path in eval_path_map.items()
    }
    for name, eval_dataset in eval_dataset.items():
        gate_load_list = []
        loader = DataLoader(
            eval_dataset, batch_size=bsz, collate_fn=fault_tolerance_data_collator
        )
        loader = accel.prepare_data_loader(loader)
        if name == "en_book":
            num_batch = 20
        else:
            num_batch = 9999999999999999
        num_batch = 1
        for batch_idx, batch in enumerate(tqdm(loader, desc=name)):
            if batch_idx >= num_batch:
                break
            outs = model(**batch, output_attentions=False, use_cache=False)
            # gate_load: (tensor([1.0, 2.3, ... num_experts]), tensor([3.0, 4.5, ... num_experts]), ... num_layers)
            gate_load = outs.gate_load
            # (num_layers, num_experts)
            gate_load = torch.stack(gate_load, dim=0).detach().cpu().numpy()
            gate_load_list.append(gate_load)
        # (num_batches, num_layers, num_experts)
        gate_load_arr = np.stack(gate_load_list, axis=0)
        # (num_layers, num_experts)
        gate_load_sum = gate_load_arr.sum(axis=0)
        np.save(result_dir / f"{name}_gate_load.npy", gate_load_sum)
        for layer_idx in range(gate_load_sum.shape[0]):
            visualize_expert_load_heatmap(
                gate_load_sum[layer_idx],
                layer_idx,
                name,
                shape=(4, 4),
                save_dir=str(result_dir),
                save_fig=True,
            )


def heatmap(
    arr: np.ndarray, xlabels: list[str], ylabels: list[str], save_path: str, title: str
):
    shape = arr.shape

    fig = plt.figure()
    ax = fig.add_subplot(111)
    im = ax.imshow(arr, cmap="OrRd", interpolation="nearest")

    for i in range(shape[0]):
        for j in range(shape[1]):
            text = ax.text(
                j,
                i,
                f"{arr[i, j]:.1%}",
                ha="center",
                va="center",
                color="black",
                fontsize=6,
            )
    ax.set_xticks(range(len(xlabels)))
    ax.set_yticks(range(len(ylabels)))
    ax.set_xticklabels(xlabels, rotation=45, ha="right")
    ax.set_yticklabels(ylabels)
    ax.set_title(title)
    fig.colorbar(im)
    fig.tight_layout()
    fig.savefig(save_path, dpi=320, bbox_inches="tight")


def calc_sim(
    # gate_load_folder = "/mnt/petrelfs/zhutong/smoe/results/llama2_7B_gradient_share_gate_load/stage1_trained_more/"
    gate_load_folder="/mnt/petrelfs/zhutong/smoe/results/llama2_7B_random_split_baseline_gate_load/",
    layer_idx=0,
    plot=True,
):
    # title = "SlimPajama"
    # sim_pairs = [["wiki", "github", "en_stack", "en_cc", "en_c4", "en_book", "en_arxiv"], ["wiki", "github", "en_stack", "en_cc", "en_c4", "en_book", "en_arxiv"]]
    # title = "Dev vs. SlimPajama"
    # sim_pairs = [
    #     ["arc_challenge", "gsm8k", "hellaswag", "mmlu"],
    #     ["en_wikipedia", "github", "en_stack", "en_cc", "en_c4", "en_book", "en_arxiv"],
    # ]
    # title = "Dev vs. Dev"
    # sim_pairs = [
    #     ["arc_challenge", "gsm8k", "hellaswag", "mmlu"],
    #     ["arc_challenge", "gsm8k", "hellaswag", "mmlu"],
    # ]
    # title = "test"
    # sim_pairs = [["wiki", "github"], ["wiki", "github"]]
    title = f"Routing Similarity Layer {layer_idx}"
    sim_pairs = [
        [
            "arc_challenge",
            "gsm8k",
            "hellaswag",
            "mmlu",
            "en_wikipedia",
            "github",
            "en_stack",
            "en_cc",
            "en_c4",
            "en_book",
            "en_arxiv",
        ],
        [
            "arc_challenge",
            "gsm8k",
            "hellaswag",
            "mmlu",
            "en_wikipedia",
            "github",
            "en_stack",
            "en_cc",
            "en_c4",
            "en_book",
            "en_arxiv",
        ],
    ]

    folder = Path(gate_load_folder)
    name2arr = {}
    suffix = "_gate_load.npy"
    for dtype in folder.glob("*" + suffix):
        name = dtype.name[: -len(suffix)]
        arr = np.load(folder / f"{name}{suffix}")
        # min-max
        name2arr[name] = arr[layer_idx] / arr[layer_idx].max()
        # # softmax
        # layer_arr = arr[layer_idx]
        # e_x = np.exp(layer_arr - layer_arr.max())
        # name2arr[name] = e_x / e_x.sum()

    sim_arr = np.zeros((len(sim_pairs[0]), len(sim_pairs[1])))
    for t1_idx, type1 in enumerate(sim_pairs[0]):
        t1_load = name2arr[type1]
        for t2_idx, type2 in enumerate(sim_pairs[1]):
            t2_load = name2arr[type2]
            _sim = np.dot(t1_load, t2_load) / (
                np.linalg.norm(t1_load) * np.linalg.norm(t2_load)
            )
            # _sim = 1.0 - np.linalg.norm(t1_load - t2_load, 2)
            sim_arr[t1_idx][t2_idx] = _sim
    if plot:
        heatmap(
            sim_arr,
            sim_pairs[1],
            sim_pairs[0],
            str(folder / f"layer{layer_idx}" / f"cos_sim_{title}.png"),
            title,
        )

    return sim_arr


def gate_load_vis():
    model_dir = "/mnt/petrelfs/share_data/quxiaoye/runs/llama2_random_scale4_112gpus_dynamic_data/outputs/cpt-llama2_random_scale4_112gpus_dynamic_data-2326233/checkpoint-5440"
    result_dir = "/mnt/petrelfs/zhutong/smoe/results/llama2_7B_random_split_sheared_sampling_fluency_80B_gate_load/"
    main(
        # w/ fluency filtering, 85b
        model_dir=model_dir,
        result_dir=result_dir,
    )

    sim_arr_list = []
    for layer_idx in range(32):
        sim_arr = calc_sim(
            gate_load_folder=result_dir,
            layer_idx=layer_idx,
        )
        sim_arr_list.append(sim_arr)
    sim_arr = np.stack(sim_arr_list, axis=0)
    sim_arr = sim_arr.mean(axis=0)
    # title = "Dev vs. SlimPajama"
    # sim_pairs = [
    #     ["arc_challenge", "gsm8k", "hellaswag", "mmlu"],
    #     ["en_wikipedia", "github", "en_stack", "en_cc", "en_c4", "en_book", "en_arxiv"],
    # ]
    title = "Routing Similarity"
    sim_pairs = [
        [
            "arc_challenge",
            "gsm8k",
            "hellaswag",
            "mmlu",
            "en_wikipedia",
            "github",
            "en_stack",
            "en_cc",
            "en_c4",
            "en_book",
            "en_arxiv",
        ],
        [
            "arc_challenge",
            "gsm8k",
            "hellaswag",
            "mmlu",
            "en_wikipedia",
            "github",
            "en_stack",
            "en_cc",
            "en_c4",
            "en_book",
            "en_arxiv",
        ],
    ]
    heatmap(
        sim_arr,
        sim_pairs[1],
        sim_pairs[0],
        f"{result_dir}/cos_sim_avg.png",
        title,
    )


def gate_load_vis_from_cache(name, cache_filepath, result_dir, minmax: bool = False):
    gate_load_sum = np.load(cache_filepath)
    if minmax:
        gate_load_sum = (gate_load_sum - gate_load_sum.min()) / (
            gate_load_sum.max() - gate_load_sum.min()
        )
    for layer_idx in range(gate_load_sum.shape[0]):
        visualize_expert_load_heatmap(
            gate_load_sum[layer_idx],
            layer_idx,
            name,
            shape=(4, 4),
            save_dir=str(result_dir),
            save_fig=True,
        )


if __name__ == "__main__":
    main(
        model_dir="/mnt/petrelfs/share_data/quxiaoye/runs/llama2_random_scale4_112gpus_dynamic_data/outputs/cpt-llama2_random_scale4_112gpus_dynamic_data-2326233/checkpoint-6120",
        result_dir="/mnt/petrelfs/zhutong/smoe/results/llama2_7B_random_split_sheared_sampling_fluency_90B_gate_load/",
    )

    # gate_load_vis()

    # for name in ["gsm8k", "mmlu"]:
    #     gate_load_vis_from_cache(
    #         name,
    #         f"results/llama2_7B_random_split_sheared_sampling_fluency_85B_gate_load/{name}_gate_load.npy",
    #         f"results/llama2_7B_random_split_sheared_sampling_fluency_85B_gate_load/{name}",
    #         minmax=True,
    #     )
