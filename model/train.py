import os
import glob
import json
import random

import numpy as np
import open3d as o3d

import torch
import torch.utils.data

from loader import split_datasets, Hand_Gestures_Dataset, MultiStreamDataLoader
from loss import CrossEntropyLoss
from model_cnn import CNN_Classifier
from transforms import PointCloud_To_RGBD
from utils import get_intrinsics

from datetime import datetime


def main():
    exp_id = '01'
    log_filename = f"train_log{exp_id}.txt"
    checkpoint_path = f"checkpoint{exp_id}.pth"

    seed = 0
    device = 'cpu' if not torch.cuda.is_available() else 'cuda'

    GESTURES_SET = (
        # "high",
        # "start",
        "select",
        # "swipe_right",
        # "swipe_left",
    )

    DATA_DIR = os.path.join(
        os.path.expanduser("~"),
        "personal",
        "gestures_navigation",
        "pc_data",
        "dataset"
    )


    CAMERAS_DIR = ("cam_center",)
    CALIBRATION_DIR = os.path.join(os.path.dirname(DATA_DIR), "calib_params")
    CALIBRATION_INTRINSIC = {
        "cam_center": "1m.json",
    }

    RENDER_OPTION = "render_option.json"

    main_camera_index = 0

    label_map = {gesture: i for i, gesture in enumerate(GESTURES_SET, start=1)}
    label_map["no_gesture"] = 0

    # frames = 5
    frames = 1

    batch_size = 1
    # max_workers = 2
    max_workers = 2

    # resized_image_size = (720, 1280)
    # resized_image_size = (72, 128)
    resized_image_size = (72, 128)  # (512, 512)
    base_fps = 30
    target_fps = 5
    # target_fps = 30

    angle = np.deg2rad(-30)
    z_target = 1.25

    # angle = np.deg2rad(0.0)
    # z_target = 0.0

    loc = np.array([0., 0., 0., 0., 0., 0.])
    scale = np.array([np.pi/24, np.pi/18, np.pi/48, 0.2, 0.1, 0.1]) / 1.5

    lr = 1e-3
    weight_decay = 0
    # weight_decay = 1e-5
    weight = None
    # weight = torch.tensor([1., 10., 10., 10., 10.])

    epochs = 1
    validate_each_epoch = 1


    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    intrinsics_paths = [os.path.join(CALIBRATION_DIR, CALIBRATION_INTRINSIC[camera])
                        for camera in CAMERAS_DIR]
    intrinsics = get_intrinsics(intrinsics_paths)[main_camera_index]

    render_option_path = os.path.join(os.path.dirname(DATA_DIR), RENDER_OPTION)

    *image_size, = map(int, intrinsics[:2])

    visualizer = o3d.visualization.Visualizer()
    visualizer.create_window(width=image_size[0], height=image_size[1])

    visualizer.get_render_option().load_from_json(render_option_path)

    pc_to_rgb = PointCloud_To_RGBD(
        batch_size,
        intrinsics,
        visualizer,
        render_option_path,
        angle=angle,
        z_target=z_target,
        loc=loc,
        scale=scale,
        image_size=resized_image_size,
    )

    data_list = [
        d for d in glob.glob(os.path.join(DATA_DIR, "G*/*/*/*"))
        if d.split(os.path.sep)[-3] in GESTURES_SET
    ]
    test_len = int(0.25 * len(data_list))
    train_len = len(data_list) - test_len
    train_list, test_list = map(list, torch.utils.data.random_split(data_list, [train_len, test_len]))

    # train_list = train_list[:21]
    # test_list = test_list[:7]
    # train_list = train_list[:2]
    # test_list = test_list[:2]

    train_datasets = split_datasets(
        Hand_Gestures_Dataset,
        batch_size=batch_size,
        max_workers=max_workers,
        path_list=train_list,
        label_map=label_map,
        transforms=pc_to_rgb,
        base_fps=base_fps,
        target_fps=target_fps,
        data_type='pcd',
    )
    train_loader = MultiStreamDataLoader(train_datasets, image_size=resized_image_size)

    test_datasets = split_datasets(
        Hand_Gestures_Dataset,
        batch_size=batch_size,
        max_workers=max_workers,
        path_list=test_list,
        label_map=label_map,
        transforms=pc_to_rgb,
        base_fps=base_fps,
        target_fps=target_fps,
        data_type='pcd',
    )
    test_loader = MultiStreamDataLoader(test_datasets, image_size=resized_image_size)

    model = CNN_Classifier(
        resized_image_size,
        frames=frames,
        batch_size=batch_size,
        num_classes=len(label_map.keys()),
    )
    model.to(device)

    if os.path.exists(checkpoint_path):
        model.load_state_dict(torch.load(checkpoint_path))

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_func = CrossEntropyLoss(weight=weight)
    loss_func.to(device)

    time = datetime.now().strftime("%Y.%m.%d %H:%M:%S")
    msg = f'{time} | Training for {epochs} epochs started!\n\
        Train set length: {len(train_list)}\n\
        Test set length: {len(test_list)}\n'.replace('  ', '')
    with open(log_filename, 'a', encoding='utf-8') as log_file:
        log_file.write(msg)
    print(msg)

    for epoch in range(epochs):

        model.train()
        train_accuracy = 0
        train_loss = 0
        n = 0

        confusion_matrix_train = torch.zeros((len(label_map.keys()), len(label_map.keys())), dtype=torch.int)

        counter = 0
        for images, labels in train_loader:
            # print(labels)
            # print(pc_paths)
            # if not all(labels):
            #     continue

            # for i, img in enumerate(pc_paths):
            #     plt.imsave(f"{i}_color.png", img[:3].permute(1, 2, 0).numpy())
            #     plt.imsave(f"{i}_depth.png", img[-1].numpy())
            counter += 1

            images = images.to(device)
            labels = labels.to(device)

            optimizer.zero_grad()

            prediction = model(images)
            loss = loss_func(prediction, labels)
            loss.backward()
            optimizer.step()

            print(f"{datetime.now().strftime('%Y.%m.%d %H:%M:%S')} TRAIN\n{epoch=}, {counter=}\n{prediction=}\n{labels=}")

            prediction_probs, prediction_labels = prediction.max(1)
            train_accuracy += (prediction_labels == labels).sum().float()
            train_loss += loss.item()
            n += len(labels)

            pred = torch.argmax(prediction, dim=1)
            for i in range(len(labels)):
                confusion_matrix_train[pred[i], labels[i]] += 1

            break

        train_accuracy /= n
        train_loss /= len(train_list)

        time = datetime.now().strftime("%Y.%m.%d %H:%M:%S")
        msg = f'{time} [Epoch: {epoch+1:02}] Train acc: {train_accuracy:.4f} | loss: {train_loss:.4f}\n\
            {confusion_matrix_train=}\n'.replace('  ', '')
        with open(log_filename, 'a', encoding='utf-8') as log_file:
            log_file.write(msg)
        print(msg)

        if (epoch+1) % validate_each_epoch == 0:
            model.eval()
            accuracy = 0
            loss = 0
            n = 0

            confusion_matrix_val = torch.zeros((len(label_map.keys()), len(label_map.keys())), dtype=torch.int)

            counter = 0
            with torch.no_grad():
                for val_images, val_labels in test_loader:
                    counter += 1

                    val_images = val_images.to(device)
                    val_labels = val_labels.to(device)

                    prediction = model(val_images)
                    prediction_probs, prediction_labels = prediction.max(1)

                    print(f"{datetime.now().strftime('%Y.%m.%d %H:%M:%S')} VAL\n{epoch=}, {counter=}\n{prediction=}\n{val_labels=}")

                    accuracy += (prediction_labels == val_labels).sum().float()
                    loss += loss_func(prediction, val_labels).item()
                    n += len(val_labels)

                    pred = torch.argmax(prediction, dim=1)
                    for i in range(len(val_labels)):
                        confusion_matrix_val[pred[i], val_labels[i]] += 1

                    break

            accuracy /= n
            loss /= len(test_list)

            time = datetime.now().strftime("%Y.%m.%d %H:%M:%S")
            msg = f'{time} [Epoch: {epoch+1:02}] Valid acc: {accuracy:.4f} | loss: {loss:.4f}\n\
                {confusion_matrix_val=}\n'.replace('  ', '')
            with open(log_filename, 'a', encoding='utf-8') as log_file:
                log_file.write(msg)
            print(msg)

            torch.save(model.state_dict(), checkpoint_path)

    visualizer.destroy_window()


if __name__ == '__main__':
    main()