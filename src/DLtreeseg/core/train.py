import os,sys
import torch
import lightning as L
from torchvision.models.detection import maskrcnn_resnet50_fpn_v2
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
import torch
import torchvision
import torch.utils.data
from torchvision.models.detection import MaskRCNN
from torchvision.models.detection.rpn import AnchorGenerator
from torch.optim import SGD
from torch.optim.lr_scheduler import StepLR
from torchvision import datasets, transforms
from torch.utils.data import DataLoader, Dataset
import numpy as np
import matplotlib.pyplot as plt
from torchvision.models import resnet101, ResNet101_Weights
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
import torchvision
import math
from tqdm import tqdm
from torchvision.transforms import v2 as T

from DLtreeseg.core import TrainDataset, PreditDataset

class MaskRCNNModule(L.LightningModule):
    def __init__(self, num_classes: int, learning_rate: float = 1e-3):
        super().__init__()
        self.model = maskrcnn_resnet50_fpn_v2(weights="DEFAULT")
        # Modify the classifier head to accommodate the custom number of classes
        in_features = self.model.roi_heads.box_predictor.cls_score.in_features
        self.model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
        in_features_mask = self.model.roi_heads.mask_predictor.conv5_mask.in_channels
        hidden_layer = 256
        self.model.roi_heads.mask_predictor = MaskRCNNPredictor(
        in_features_mask,
        hidden_layer,
        num_classes
        )
        # Hyperparameters
        self.learning_rate = learning_rate
    def forward(self, images, targets=None):
        return self.model(images, targets)

    def training_step(self, batch, batch_idx):
        images, targets = batch
        loss_dict = self(images, targets)
        loss = sum(loss for loss in loss_dict.values())
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        images, targets = batch
        loss_dict = self(images, targets)
        loss = sum(loss for loss in loss_dict.values())
        self.log('val_loss', loss)
        return loss

    def configure_optimizers(self):
        optimizer = torch.optim.SGD(self.parameters(), lr=self.learning_rate, momentum=0.9)
        return optimizer
    
class MaskRCNNLightning(L.LightningModule):
    def __init__(self, num_classes):
        super(MaskRCNNLightning, self).__init__()
        self.model = self._create_model(num_classes)
        self.lr = 0.005  # Learning rate

    def _create_model(self, num_classes):
        # Load a pre-trained Mask R-CNN model with ResNet-101 backbone
        backbone = torchvision.models.resnet101(ResNet101_Weights.DEFAULT)
        backbone = torch.nn.Sequential(*list(backbone.children())[:-2])  # Remove the last layers
        backbone.out_channels = 2048  # Set output channels

        # Create anchor generator for RPN
        rpn_anchor_generator = AnchorGenerator(
            sizes=((32, 64, 128, 256, 512),),
            aspect_ratios=((0.5, 1.0, 2.0),) * 5
        )

        # Create the Mask R-CNN model
        model = MaskRCNN(
            backbone,
            num_classes=num_classes,
            rpn_anchor_generator=rpn_anchor_generator,
            box_detections_per_img=100
        )
        return model

    def forward(self, images, targets=None):
        return self.model(images, targets)

    def training_step(self, batch, batch_idx):
        images, targets = batch
        loss_dicts = self(images, targets)
        total_loss = 0
        if isinstance(loss_dicts, list):
            for loss_dict in loss_dicts:
                total_loss += sum(loss for loss in loss_dict.values())
            self.log('train_loss', total_loss)
        else: 
            total_loss = sum(loss for loss in loss_dicts.values())
        return total_loss

    def validation_step(self, batch, batch_idx):
        images, targets = batch
        loss_dicts = self(images, targets)
        total_loss = 0
        if isinstance(loss_dicts, list):
            for loss_dict in loss_dicts:
                total_loss += sum(loss for loss in loss_dict.values())
            self.log('train_loss', total_loss)
        else: 
            total_loss = sum(loss for loss in loss_dicts.values())
        self.log('val_loss', total_loss)

    def configure_optimizers(self):
        optimizer = SGD(self.model.parameters(), lr=self.lr, momentum=0.9, weight_decay=0.0005)
        lr_scheduler = StepLR(optimizer, step_size=3, gamma=0.1)
        return [optimizer], [lr_scheduler]

def mask_rcnn(num_classes):
    # Load an instance segmentation model pre-trained on COCO
    model = torchvision.models.detection.maskrcnn_resnet50_fpn_v2(weights='DEFAULT')

    # Get the number of input features for the classifier
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    # Replace the pre-trained head with a new one
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)

    # Get the number of input features for the mask classifier
    in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
    hidden_layer = 256
    # Replace the mask predictor with a new one
    model.roi_heads.mask_predictor = MaskRCNNPredictor(in_features_mask, hidden_layer, num_classes)

    return model

def train_one_epoch(model, optimizer, data_loader, device, epoch):
    model.train()
    lr_scheduler = None
    
    # Build a dictionary to recored losses
    LOSS = {
        'loss_classifier':[],
        'loss_box_reg':[],
        'loss_mask':[],
        'loss_objectness':[],
        'loss_rpn_box_reg':[],
        'loss_sum':[]
    }
    
    # Warm up the model in the first epoch
    if epoch == 0:
        warmup_factor = 1.0 / 1000
        warmup_iters = min(1000, len(data_loader) - 1)
        lr_scheduler = torch.optim.lr_scheduler.LinearLR(
            optimizer, start_factor=warmup_factor, total_iters=warmup_iters
        )
        
    # Training process
    for images, targets in tqdm(data_loader):
        images = list(image.to(device) for image in images)
        targets = [{k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in t.items()} for t in targets]
        loss_dict = model(images, targets)
        for i in loss_dict.keys():
            LOSS[i].append(loss_dict[i].item())    
        losses = sum(loss for loss in loss_dict.values())
        loss_value = losses.item()
        LOSS['loss_sum'].append(loss_value)

        if not math.isfinite(loss_value):
            print(f"Loss is {loss_value}, stopping training")
            print(loss_dict)
            sys.exit(1)

        optimizer.zero_grad()
        losses.backward()
        optimizer.step()

        if lr_scheduler is not None:
            lr_scheduler.step()

    return LOSS

def val_one_epoch(model, data_loader, device, epoch):
    # Build a dictionary to recored losses
    LOSS = {
        'val_loss_classifier':[],
        'val_loss_box_reg':[],
        'val_loss_mask':[],
        'val_loss_objectness':[],
        'val_loss_rpn_box_reg':[],
        'val_loss_sum':[]
    }
    
    # Speed ​​up evaluation by not computing gradients
    with torch.no_grad():
        model.train()
        
        # Evaluating process
        for images, targets in tqdm(data_loader):
            images = list(image.to(device) for image in images)
            targets = [{k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in t.items()} for t in targets]
            loss_dict = model(images, targets)
            for i in loss_dict.keys():
                LOSS["val_"+i].append(loss_dict[i].item())    
            losses = sum(loss for loss in loss_dict.values())
            loss_value = losses.item()
            LOSS['val_loss_sum'].append(loss_value)

    return LOSS

# Define dataloader's collate function
def my_collate_fn(batch):
    return tuple(zip(*batch))

def train_model(coco_path, model_name, num_classes, batch_size, learning_rate, num_epochs):
    
    # Split dataset into train dataset and valid dataset
    dataset = TrainDataset(coco_path)
    train_set, val_set = torch.utils.data.random_split(dataset, [0.8, 0.2], generator=torch.Generator().manual_seed(0))

    # Load datasets to dataloaders
    data_loader_train = torch.utils.data.DataLoader(train_set, batch_size, shuffle=True, collate_fn=my_collate_fn)
    data_loader_val = torch.utils.data.DataLoader(val_set, batch_size, shuffle=True, collate_fn=my_collate_fn)
    
    # Set caculating deivce
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    print(f"training device is {device}")

    # Move model to device
    model = mask_rcnn(num_classes+1)
    model.to(device)

    # Construct an optimizer
    model_parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.SGD(model_parameters, lr=learning_rate, momentum=0.9, weight_decay=0.0005)

    # Set a learning rate scheduler (defualt value is a constant learning rate)
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=1)

    # Build a dictionary to recored losses
    LOSSES = {
            'loss_classifier':[],
            'loss_box_reg':[],
            'loss_mask':[],
            'loss_objectness':[],
            'loss_rpn_box_reg':[],
            'loss_sum':[],
            'val_loss_classifier':[],
            'val_loss_box_reg':[],
            'val_loss_mask':[],
            'val_loss_objectness':[],
            'val_loss_rpn_box_reg':[],
            'val_loss_sum':[]
        }
    
    # Declare the checkpoint saving paths
    PATH = model_name + '_checkpoint.pt'
    min_PATH = model_name + '_checkpoint_min.pt'

    # Declare a minimum loss value to ensure whether the current epoch has the minimum loss
    min_loss = None

    # Training process
    for epoch in range(num_epochs):
        print(f"epoch {epoch} is training - learning rate = {lr_scheduler.get_last_lr()[0]}")  
        # Train for one epoch and recored train losses
        LOSSES_train = train_one_epoch(model, optimizer, data_loader_train, device, epoch)
        
        # Update the learning rate
        lr_scheduler.step()
        
        print(f"epoch {epoch} is validating")
        # valid for one epoch and recored valid losses
        LOSSES_val = val_one_epoch(model, data_loader_val, device, epoch)
        plt.figure(epoch)
        plt.suptitle(f"Training Loss till epoch {epoch}")
        
        for i, v in enumerate(LOSSES_train.keys()):
            LOSSES[v].append(sum(LOSSES_train[v])/len(LOSSES_train[v]))
            plt.subplot(3, 2, i+1)
            plt.plot(LOSSES[v], label=v, color='b')
            plt.title(v)
        
        for i, v in enumerate(LOSSES_val.keys()):
            LOSSES[v].append(sum(LOSSES_val[v])/len(LOSSES_val[v]))
            plt.subplot(3, 2, i+1)
            plt.plot(LOSSES[v], label=v, color='r')
            plt.legend(fontsize='xx-small', loc='upper right') 
        
        plt.tight_layout()
        plt.savefig(model_name + "_losses_curve.png", dpi=600)
        ##plt.show(block=False)
        ##plt.pause(2) 
        
        # Print out current train loss and valid loss
        print(f"train loss sum = {sum(LOSSES_train['loss_sum'])/len(LOSSES_train['loss_sum'])}")
        print(f"valid loss sum = {sum(LOSSES_val['val_loss_sum'])/len(LOSSES_val['val_loss_sum'])}\n")
        
        # If the loss for the current epoch is minimal, save it to the checkpoint
        if not min_loss or LOSSES['val_loss_sum'][-1] < min_loss:
            torch.save({
                    'epoch': epoch,
                    'model_state_dict': model.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict(),
                    'loss': LOSSES,
                    }, min_PATH)
            min_loss = LOSSES['val_loss_sum'][-1]
        
        # Save training datas to the checkpoint every 5 epoch
        if (epoch+1)%5 == 0:
            torch.save({
                    'epoch': epoch,
                    'model_state_dict': model.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict(),
                    'loss': LOSSES,
                    }, PATH)
            
        # Save the final model 
        torch.save(model,  model_name + '.pt')



if __name__=='__main__':
    
    train_model(coco_path="cocopath", model_name='my_model', transform=get_transform(train=True), num_classes=1, batch_size=1, learning_rate=0.0001, num_epochs=20)