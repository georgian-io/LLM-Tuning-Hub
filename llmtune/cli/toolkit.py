import logging
import shutil
from pathlib import Path

import torch
import typer
import yaml
from pydantic import ValidationError
from transformers import utils as hf_utils
from typing_extensions import Annotated

import llmtune
from llmtune.constants.files import EXAMPLE_CONFIG_FNAME
from llmtune.data.dataset_generator import DatasetGenerator
from llmtune.finetune.lora import LoRAFinetune
from llmtune.inference.lora import LoRAInference
from llmtune.pydantic_models.config_model import Config
from llmtune.ui.rich_ui import RichUI
from llmtune.utils.ablation_utils import generate_permutations
from llmtune.utils.save_utils import DirectoryHelper


hf_utils.logging.set_verbosity_error()
torch._logging.set_logs(all=logging.CRITICAL)


app = typer.Typer()
generate_app = typer.Typer()

app.add_typer(
    generate_app,
    name="generate",
    help="Generate various artefacts, such as config files",
)


def run_one_experiment(config: Config, config_path: Path) -> None:
    dir_helper = DirectoryHelper(config_path, config)

    # Loading Data -------------------------------
    RichUI.before_dataset_creation()

    with RichUI.during_dataset_creation("Injecting Values into Prompt", "monkey"):
        dataset_generator = DatasetGenerator(**config.data.model_dump())

    _ = dataset_generator.train_columns
    test_columns = dataset_generator.test_columns

    dataset_path = dir_helper.save_paths.dataset
    if not dataset_path.exists():
        train, test = dataset_generator.get_dataset()
        dataset_generator.save_dataset(dataset_path)
    else:
        RichUI.dataset_found(dataset_path)
        train, test = dataset_generator.load_dataset_from_pickle(dataset_path)

    RichUI.dataset_display_one_example(train[0], test[0])
    RichUI.after_dataset_creation(dataset_path, train, test)

    # Loading Model -------------------------------
    RichUI.before_finetune()

    weights_path = dir_helper.save_paths.weights

    # model_loader = ModelLoader(config, console, dir_helper)
    if not weights_path.exists() or not any(weights_path.iterdir()):
        finetuner = LoRAFinetune(config, dir_helper)
        with RichUI.during_finetune():
            finetuner.finetune(train)
        finetuner.save_model()
        RichUI.after_finetune()
    else:
        RichUI.finetune_found(weights_path)

    # Inference -------------------------------
    RichUI.before_inference()
    results_path = dir_helper.save_paths.results
    results_file_path = dir_helper.save_paths.results_file
    if not results_file_path.exists():
        inference_runner = LoRAInference(test, test_columns, config, dir_helper)
        inference_runner.infer_all()
        RichUI.after_inference(results_path)
    else:
        RichUI.results_found(results_path)

    # QA -------------------------------
    # RichUI.before_qa()
    # qa_path = dir_helper.save_paths.qa
    # if not exists(qa_path) or not listdir(qa_path):
    #     # TODO: Instantiate unit test classes
    #     # TODO: Load results.csv
    #     # TODO: Run Unit Tests
    #     # TODO: Save Unit Test Results
    #     pass


@app.command("run")
def run(config_path: Annotated[str, typer.Argument(help="Path of the config yaml file")] = "./config.yml") -> None:
    """Run the entire exmperiment pipeline"""
    # Load YAML config
    with Path(config_path).open("r") as file:
        config = yaml.safe_load(file)
        configs = (
            generate_permutations(config, Config) if config.get("ablation", {}).get("use_ablate", False) else [config]
        )
    for config in configs:
        # validate data with pydantic
        try:
            config = Config(**config)
        except ValidationError as e:
            print(e.json())

        dir_helper = DirectoryHelper(config_path, config)

        # Reload config from saved config
        with dir_helper.save_paths.config_file.open("r") as file:
            config = yaml.safe_load(file)
            config = Config(**config)

        run_one_experiment(config, config_path)


@generate_app.command("config")
def generate_config():
    """
    Generate an example `config.yml` file in current directory
    """
    module_path = Path(llmtune.__file__)
    example_config_path = module_path.parent / EXAMPLE_CONFIG_FNAME
    destination = Path.cwd()
    shutil.copy(example_config_path, destination)
    RichUI.generate_config(EXAMPLE_CONFIG_FNAME)


def cli():
    app()
