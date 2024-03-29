import argparse
from collections import OrderedDict
import os
import time

from PIL import Image
import torch
from torch.utils.data import DataLoader
from torch.autograd import Variable
import torchvision.transforms as transforms
from tqdm.auto import tqdm

from data.dataset import UnpairedDepthDataset
from models.model import Generator, GlobalGenerator2, InceptionV3
from models import networks
import utils.util as util
from utils.visualizer2 import Visualizer
from utils.utils import channel2width, createNRandompatches, LambdaLR, weights_init_normal

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    # Basic configuration
    parser.add_argument("--name", type=str, help="name of this experiment")
    parser.add_argument("--checkpoints_dir", type=str, default="checkpoints", help="Where checkpoints are saved")
    parser.add_argument("--epoch", type=int, default=0, help="starting epoch")
    parser.add_argument("--n_epochs", type=int, default=200, help="number of epochs of training")
    parser.add_argument("--batch_size", type=int, default=6, help="size of the batches")
    parser.add_argument("--cuda", action="store_true", help="use GPU computation", default=True)
    parser.add_argument("--n_cpu", type=int, default=8, help="number of cpu threads to use during batch generation")
    parser.add_argument("--wandb", type=int, default=1, help="log with W&B")

    # Loading data
    parser.add_argument("--full_color_dir", type=str, default="datasets/vangogh2photo/",
                        help="photograph directory root directory")
    parser.add_argument("--flat_color_dir", type=str, default="", help="line drawings dataset root directory")
    parser.add_argument("--depth_maps_dir", type=str, default="", help="dataset of corresponding ground truth depth maps")
    parser.add_argument("--feats2Geom_path", type=str, default="checkpoints/feats2Geom/feats2depth.pth",
                        help="path to pretrained features to depth map network")

    # Model architecture & optimizer
    parser.add_argument("--lr", type=float, default=0.0002, help="initial learning rate")
    parser.add_argument("--momentum", type=float, default=0.9, help="momentum for optimizer")
    parser.add_argument("--decay_epoch", type=int, default=100,
                        help="epoch to start linearly decaying the learning rate to 0")
    parser.add_argument("--size", type=int, default=256, help="size of the data crop (squared assumed)")
    parser.add_argument("--input_nc", type=int, default=3, help="number of channels of input data")
    parser.add_argument("--output_nc", type=int, default=3, help="number of channels of output data")
    parser.add_argument("--geom_nc", type=int, default=3, help="number of channels of geom data")
    parser.add_argument("--num_gen_filters", type=int, default=64, help="# of gen filters in first conv layer")
    parser.add_argument("--num_discrim_filters", type=int, default=64, help="# of discrim filters in first conv layer")
    parser.add_argument("--netD", type=str, default="basic", help="selects model to use for netD")
    parser.add_argument("--n_blocks", type=int, default=3, help="number of resnet blocks for generator")
    parser.add_argument("--n_layers_D", type=int, default=3, help="only used if netD==n_layers")
    parser.add_argument("--norm", type=str, default="instance", help="instance normalization or batch normalization")
    parser.add_argument("--disc_sigmoid", type=int, default=0, help="use sigmoid in disc loss")
    parser.add_argument("--every_feat", type=int, default=1, help="use transfer features for recog loss")
    parser.add_argument("--finetune_netGeom", type=int, default=1, help="make geometry networks trainable")

    # Loading model from checkpoints
    parser.add_argument("--load_pretrain", type=str, default="", help="where to load file if wanted")
    parser.add_argument("--continue_train", action="store_true", help="continue training: load the latest model")
    parser.add_argument("--which_epoch", type=str, default="latest", help="which epoch to load from if continue_train")

    # Dataset options
    parser.add_argument("--mode", type=str, default="train", help="train, val, test, etc")
    parser.add_argument("--load_size", type=int, default=286, help="scale images to this size")
    parser.add_argument("--crop_size", type=int, default=256, help="then crop to this size")
    parser.add_argument("--aspect_ratio", type=float, default=1.0,
                        help="The ratio width/height. The final height of the load image will be crop_size/aspect_ratio")
    parser.add_argument("--max_dataset_size", type=int, default=float("inf"),
                        help="Maximum number of samples allowed per dataset. If the dataset directory contains more than max_dataset_size, only a subset is loaded.")
    parser.add_argument("--preprocess", type=str, default="resize_and_crop",
                        help="scaling and cropping of images at load time [resize_and_crop | crop | scale_width | scale_width_and_crop | none]")
    parser.add_argument("--no_flip", action="store_true",
                        help="if specified, do not flip the images for data augmentation")

    # Loss functions weights
    parser.add_argument("--cond_cycle", type=float, default=1.0, help="weight of the appearance reconstruction loss")
    parser.add_argument("--cond_GAN", type=float, default=1.0, help="weight of the adversarial style loss")
    parser.add_argument("--cond_recog", type=float, default=10.0, help="weight of the semantic loss")
    parser.add_argument("--cond_geom", type=float, default=10.0, help="weight of the geometry style loss")

    # Geometry loss options
    parser.add_argument("--use_geom", type=int, default=1, help="include the geometry loss")
    parser.add_argument("--midas", type=int, default=1, help="use midas depth map")

    parser.add_argument("--use_sketch", type=int, default=1, help="include the sketch loss")

    # Semantic loss options
    parser.add_argument("--use_clip", type=int, default=1, help="include the CLIP semantics loss")
    parser.add_argument("--N_patches", type=int, default=1, help="number of patches for clip")
    parser.add_argument("--patch_size", type=int, default=128, help="patchsize for clip")
    parser.add_argument("--num_classes", type=int, default=55, help="number of classes for inception")
    parser.add_argument("--cos_clip", type=int, default=0, help="use cosine similarity for CLIP semantic loss")

    # Model save options
    parser.add_argument("--save_epoch_freq", type=int, default=1000, help="how often to save the latest model in steps")
    parser.add_argument("--slow", type=int, default=0, help="only frequently save netG_A, netGeom")
    parser.add_argument("--log_int", type=int, default=50, help="display frequency for tensorboard")

    opt = parser.parse_args()
    print(opt)

    checkpoints_dir = opt.checkpoints_dir
    name = opt.name

    # Weights & Biases set up
    if opt.wandb == 1:
        import wandb
        wandb.login()
        run = wandb.init(project="Anime2Cartoon", config=vars(opt))

    tensor2im = util.tensor2imv2
    visualizer = Visualizer(checkpoints_dir, name, tf_log=True, isTrain=True)
    print("Created visualizer")

    if torch.cuda.is_available() and not opt.cuda:
        print("WARNING: You have a CUDA device, but you are not currently using it; please run this file with --cuda.")

    gen_A = Generator(opt.input_nc, opt.output_nc, opt.n_blocks)
    gen_B = Generator(opt.output_nc, opt.input_nc, opt.n_blocks)

    if opt.use_geom == 1:
        net_geom = GlobalGenerator2(768, opt.geom_nc, n_downsampling=1, n_UPsampling=3)
        net_geom.load_state_dict(torch.load(opt.feats2Geom_path))
        print(f"Loading pretrained features to depth network from {opt.feats2Geom_path}.")
        if opt.finetune_netGeom == 0:
            net_geom.eval()
    else:
        opt.finetune_netGeom = 0

    if opt.use_sketch == 1:
        net_sketch = Generator(opt.input_nc, 1, opt.n_blocks)
        # Load state dicts
        net_sketch.load_state_dict(torch.load("checkpoints/anime_style/netG_A_latest.pth"))
        print("Loaded pretrained sketch network.")
        net_sketch.eval()

    disc_input_nc_A = opt.input_nc
    disc_input_nc_B = opt.output_nc

    disc_A = networks.define_D(disc_input_nc_A, opt.num_discrim_filters, opt.netD, opt.n_layers_D, opt.norm,
                               use_sigmoid=False)
    disc_B = networks.define_D(disc_input_nc_B, opt.num_discrim_filters, opt.netD, opt.n_layers_D, opt.norm,
                               use_sigmoid=False)

    if opt.cuda:
        device = "cuda"
        gen_A.cuda()
        gen_B.cuda()
        disc_A.cuda()
        disc_B.cuda()
        if opt.use_geom == 1:
            net_geom.cuda()
        if opt.use_sketch:
            net_sketch.cuda()
    else:
        device = "cpu"

    # Load pretrained Inception
    net_recog = InceptionV3(opt.num_classes, opt.mode=="test", use_aux=True, pretrain=True, freeze=True,
                            every_feat=opt.every_feat == 1)
    net_recog.cuda()
    net_recog.eval()

    if opt.use_clip:
        import clip
        clip_model, preprocess = clip.load("ViT-B/32", device=device, jit=False)
        # Convert applicable model parameters to fp16
        clip.model.convert_weights(clip_model)

    # Load in progress weights if continue train or load_pretrain.
    if opt.continue_train:
        gen_A.load_state_dict(
            torch.load(os.path.join(opt.checkpoints_dir, opt.name, "netG_A_%s.pth" % opt.which_epoch)))
        gen_B.load_state_dict(
            torch.load(os.path.join(opt.checkpoints_dir, opt.name, "netG_B_%s.pth" % opt.which_epoch)))
        disc_A.load_state_dict(
            torch.load(os.path.join(opt.checkpoints_dir, opt.name, "netD_A_%s.pth" % opt.which_epoch)))
        disc_B.load_state_dict(
            torch.load(os.path.join(opt.checkpoints_dir, opt.name, "netD_B_%s.pth" % opt.which_epoch)))
        if opt.finetune_netGeom == 1:
            net_geom.load_state_dict(
                torch.load(os.path.join(opt.checkpoints_dir, opt.name, "netGeom_%s.pth" % opt.which_epoch)))
        print("Loaded %s from " % opt.which_epoch + os.path.join(checkpoints_dir, name))
    elif len(opt.load_pretrain) > 0:
        pretrained_path = opt.load_pretrain
        gen_A.load_state_dict(torch.load(os.path.join(pretrained_path, "netG_A_%s.pth" % opt.which_epoch)))
        gen_B.load_state_dict(torch.load(os.path.join(pretrained_path, "netG_B_%s.pth" % opt.which_epoch)))
        disc_A.load_state_dict(torch.load(os.path.join(pretrained_path, "netD_A_%s.pth" % opt.which_epoch)))
        disc_B.load_state_dict(torch.load(os.path.join(pretrained_path, "netD_B_%s.pth" % opt.which_epoch)))
        if opt.finetune_netGeom == 1:
            net_geom.load_state_dict(torch.load(os.path.join(pretrained_path, "netGeom_%s.pth" % opt.which_epoch)))
        print("Loaded %s from " % opt.which_epoch + " " + pretrained_path)
    else:
        gen_A.apply(weights_init_normal)
        gen_B.apply(weights_init_normal)
        disc_A.apply(weights_init_normal)
        disc_B.apply(weights_init_normal)

    print("Loaded networks!")

    # Losses
    criterionGAN = networks.GANLoss(use_lsgan=True, target_real_label=1.0,
                                    target_fake_label=0.0, calculate_mean=True).to(device)

    criterionCycle = torch.nn.L1Loss()
    criterionCycleB = criterionCycle

    if opt.use_clip:
        criterionCLIP = torch.nn.MSELoss(reduce=True)
        if opt.cos_clip == 1:
            criterionCLIP = torch.nn.CosineSimilarity(dim=1, eps=1e-08)

    criterionGeom = torch.nn.BCELoss(reduce=True)

    # Only use B to A.
    optimizer_G_A = torch.optim.Adam(gen_A.parameters(), lr=opt.lr, betas=(0.5, 0.999))
    optimizer_G_B = torch.optim.Adam(gen_B.parameters(), lr=opt.lr, betas=(0.5, 0.999))

    if opt.use_geom == 1 and opt.finetune_netGeom == 1:
        optimizer_Geom = torch.optim.Adam(net_geom.parameters(), lr=opt.lr, betas=(0.5, 0.999))

    optimizer_D_B = torch.optim.Adam(disc_B.parameters(), lr=opt.lr, betas=(0.5, 0.999))
    optimizer_D_A = torch.optim.Adam(disc_A.parameters(), lr=opt.lr, betas=(0.5, 0.999))

    lr_scheduler_G_A = torch.optim.lr_scheduler.LambdaLR(optimizer_G_A,
                                                         lr_lambda=LambdaLR(opt.n_epochs, opt.epoch,
                                                                            opt.decay_epoch).step)
    lr_scheduler_D_B = torch.optim.lr_scheduler.LambdaLR(optimizer_D_B,
                                                         lr_lambda=LambdaLR(opt.n_epochs, opt.epoch,
                                                                            opt.decay_epoch).step)

    lr_scheduler_G_B = torch.optim.lr_scheduler.LambdaLR(optimizer_G_B,
                                                         lr_lambda=LambdaLR(opt.n_epochs, opt.epoch,
                                                                            opt.decay_epoch).step)
    lr_scheduler_D_A = torch.optim.lr_scheduler.LambdaLR(optimizer_D_A,
                                                         lr_lambda=LambdaLR(opt.n_epochs, opt.epoch,
                                                                            opt.decay_epoch).step)

    # Inputs & targets memory allocation
    Tensor = torch.cuda.FloatTensor if opt.cuda else torch.Tensorreal_A

    # Dataset loader
    # Image.BICUBIC produces higher-quality images than BILINEAR, but is slower.
    transform = [transforms.Resize(int(opt.size * 1.12), Image.BICUBIC),
                 transforms.RandomCrop(opt.size),
                 transforms.ToTensor()]

    train_ds = UnpairedDepthDataset(opt.full_color_dir, opt.flat_color_dir, opt, transform=transform,
                                    mode=opt.mode, midas=opt.midas > 0, depthroot=opt.depth_maps_dir, sketchroot="examples/train/line_drawings")

    train_dataloader = DataLoader(train_ds, batch_size=opt.batch_size, shuffle=True, num_workers=opt.n_cpu,
                                  drop_last=True)

    print("Loaded %d images" % len(train_ds))

    # Training
    for epoch in range(opt.epoch, opt.n_epochs):
        start_time = time.time()

        pbar = tqdm(enumerate(train_dataloader), total=len(train_dataloader))
        for i, batch in pbar:
            total_steps = epoch * len(train_dataloader) + i

            img_r = Variable(batch["r"]).cuda()
            img_depth = Variable(batch["depth"]).cuda()

            real_A = img_r
            labels = Variable(batch["label"]).cuda()

            real_B = Variable(batch["line"]).cuda()

            recover_geom = img_depth
            batch_size = real_A.size()[0]

            cond_GAN = opt.cond_GAN
            cond_recog = opt.cond_recog
            cond_cycle = opt.cond_cycle

            # Generator

            fake_B = gen_A(real_A)  # G_A(A)
            rec_A = gen_B(fake_B)  # G_B(G_A(A))

            fake_A = gen_B(real_B)  # G_B(B)
            rec_B = gen_A(fake_A)  # G_A(G_B(B))

            loss_cycle_Geom = 0
            if opt.use_geom == 1:
                geom_input = fake_B
                if geom_input.size()[1] == 1:
                    geom_input = geom_input.repeat(1, 3, 1, 1)
                _, geom_input = net_recog(geom_input)

                pred_geom = net_geom(geom_input)

                pred_geom = (pred_geom + 1) / 2.0  ###[-1, 1] ---> [0, 1]

                loss_cycle_Geom = criterionGeom(pred_geom, recover_geom)

            if opt.use_sketch == 1:
                geom_input = fake_B
                if geom_input.size()[1] == 1:
                    geom_input = geom_input.repeat(1, 3, 1, 1)
                gt_sketch = recover_geom
                from torchvision.utils import save_image
                save_image(geom_input[0], "test/geom_input.png")
                save_image(gt_sketch[0], "test/gt_sketch.png")
                pred_geom = net_sketch(geom_input)
                save_image(pred_geom[0], "test/pred_geom.png")
                loss_cycle_Geom = criterionGeom(pred_geom, gt_sketch)

            ########## loss A Reconstruction ##########

            loss_G_A = criterionGAN(disc_A(fake_A), True)

            # GAN loss D_B(G_B(B))
            pred_fake_GAN = disc_B(fake_B)
            loss_G_B = criterionGAN(disc_B(fake_B), True)

            # Forward cycle loss || G_B(G_A(A)) - A||
            loss_cycle_A = criterionCycle(rec_A, real_A)
            loss_cycle_B = criterionCycleB(rec_B, real_B)
            # combined loss and calculate gradients

            loss_GAN = loss_G_A + loss_G_B
            loss_RC = loss_cycle_A + loss_cycle_B

            loss_G = cond_cycle * loss_RC + cond_GAN * loss_GAN
            loss_G += opt.cond_geom * loss_cycle_Geom

            # renormalize mean=(0.48145466, 0.4578275, 0.40821073), std=(0.26862954, 0.26130258, 0.27577711)
            recog_real = real_A
            # recog_real0 = (recog_real[:, 0, :, :].unsqueeze(1) - 0.48145466) / 0.26862954
            # recog_real1 = (recog_real[:, 1, :, :].unsqueeze(1) - 0.4578275) / 0.26130258
            # recog_real2 = (recog_real[:, 2, :, :].unsqueeze(1) - 0.40821073) / 0.27577711
            # recog_real = torch.cat([recog_real0, recog_real1, recog_real2], dim=1)

            line_input = fake_B
            if opt.output_nc == 1:
                line_input_channel0 = (line_input - 0.48145466) / 0.26862954
                line_input_channel1 = (line_input - 0.4578275) / 0.26130258
                line_input_channel2 = (line_input - 0.40821073) / 0.27577711
                line_input = torch.cat([line_input_channel0, line_input_channel1, line_input_channel2], dim=1)

            patches_r = [torch.nn.functional.interpolate(recog_real, size=224)]  # The resize operation on tensor.
            patches_l = [torch.nn.functional.interpolate(line_input, size=224)]

            # Patch based clip loss
            if opt.N_patches > 1:
                patches_r2, patches_l2 = createNRandompatches(recog_real, line_input, opt.N_patches, opt.patch_size)
                patches_r += patches_r2
                patches_l += patches_l2

            # Semantic loss
            if opt.use_clip:
                loss_recog = 0
                for patchnum in range(len(patches_r)):
                    real_patch = patches_r[patchnum]
                    line_patch = patches_l[patchnum]

                    feats_r = clip_model.encode_image(real_patch).detach()
                    feats_line = clip_model.encode_image(line_patch)

                    myloss_recog = criterionCLIP(feats_line, feats_r.detach())
                    if opt.cos_clip == 1:
                        myloss_recog = 1.0 - myloss_recog
                        myloss_recog = torch.mean(myloss_recog)

                    patch_factor = (1.0 / float(opt.N_patches))
                    if patchnum == 0:
                        patch_factor = 1.0
                    loss_recog += patch_factor * myloss_recog

                loss_G += cond_recog * loss_recog

            if i % 16 == 0:
                optimizer_G_A.zero_grad()
                optimizer_G_B.zero_grad()
                if opt.finetune_netGeom == 1:
                    optimizer_Geom.zero_grad()

            loss_G.backward()

            if i % 16 == 0:
                optimizer_G_A.step()
                optimizer_G_B.step()
                if opt.finetune_netGeom == 1:
                    optimizer_Geom.step()

            # Discriminator A

            # Fake loss
            pred_fake_A = disc_A(fake_A.detach())
            loss_D_A_fake = criterionGAN(pred_fake_A, False)

            # Real loss

            pred_real_A = disc_A(real_A)
            loss_D_A_real = criterionGAN(pred_real_A, True)

            # Total loss
            loss_D_A = torch.mean(cond_GAN * (loss_D_A_real + loss_D_A_fake)) * 0.5

            if i % 16 == 0:
                optimizer_D_A.zero_grad()
            loss_D_A.backward()
            if i % 16 == 0:
                optimizer_D_A.step()

            # Discriminator B

            # Fake loss
            pred_fake_B = disc_B(fake_B.detach())
            loss_D_B_fake = criterionGAN(pred_fake_B, False)

            # Real loss

            pred_real_B = disc_B(real_B)
            loss_D_B_real = criterionGAN(pred_real_B, True)

            # Total loss
            loss_D_B = torch.mean(cond_GAN * (loss_D_B_real + loss_D_B_fake)) * 0.5

            if i % 16 == 0:
                optimizer_D_B.zero_grad()
            loss_D_B.backward()
            if i % 16 == 0:
                optimizer_D_B.step()

            # Progress report
            if (i + 1) % opt.log_int == 0:
                errors = {}

                errors["total_G"] = loss_G.item() if not isinstance(loss_G, (int, float)) else loss_G
                errors["loss_RC"] = torch.mean(loss_RC) if not isinstance(loss_RC, (int, float)) else loss_RC
                if opt.use_geom:
                    errors["loss_cycle_Geom"] = torch.mean(loss_cycle_Geom) if not isinstance(loss_cycle_Geom,
                                                                                              (int,
                                                                                               float)) else loss_cycle_Geom
                if opt.use_sketch:
                    errors["loss_cycle_sketch"] = torch.mean(loss_cycle_Geom) if not isinstance(loss_cycle_Geom,
                                                                                              (int,
                                                                                               float)) else loss_cycle_Geom
                errors["loss_GAN"] = torch.mean(loss_GAN)
                errors["loss_D_B"] = loss_D_B.item() if not isinstance(loss_D_B, (int, float)) else loss_D_B
                errors["loss_D_A"] = loss_D_A.item() if not isinstance(loss_D_A, (int, float)) else loss_D_A
                if opt.use_clip:
                    errors["loss_recog"] = torch.mean(loss_recog) if not isinstance(loss_recog,
                                                                                    (int, float)) else loss_recog

                end_time = time.time()
                elapsed_time = round(end_time - start_time, 1)

                if opt.wandb == 1:
                    visualizer.print_current_errors(epoch, total_steps, errors, elapsed_time, is_wandb=True)
                else:
                    visualizer.print_current_errors(epoch, total_steps, errors, elapsed_time, is_wandb=False)
                visualizer.plot_current_errors(errors, total_steps)

                with torch.no_grad():
                    input_img = channel2width(real_A)
                    if opt.use_geom == 1:
                        pred_geom = channel2width(pred_geom)
                        input_img = torch.cat([input_img, channel2width(recover_geom)], dim=3)

                    input_img_fake = channel2width(fake_A)
                    rec_A = channel2width(rec_A)

                    show_real_B = real_B

                    visuals = OrderedDict([("real_A", tensor2im(input_img.data[0])),
                                           ("real_B", tensor2im(show_real_B.data[0])),
                                           ("fake_A", tensor2im(input_img_fake.data[0])),
                                           ("rec_A", tensor2im(rec_A.data[0])),
                                           ("fake_B", tensor2im(fake_B.data[0]))])

                    if opt.use_geom == 1:
                        visuals["pred_geom"] = tensor2im(pred_geom.data[0])

                    visualizer.display_current_results(visuals, total_steps, epoch)

        # Update learning rates
        lr_scheduler_G_A.step()
        lr_scheduler_G_B.step()
        lr_scheduler_D_A.step()
        lr_scheduler_D_B.step()

        # Save models checkpoints
        # torch.save(netG_A2B.state_dict(), "output/netG_A2B.pth")
        if (epoch + 1) % opt.save_epoch_freq == 0:
            torch.save(gen_A.state_dict(), os.path.join(opt.checkpoints_dir, name, "netG_A_%02d.pth" % (epoch)))
            if opt.finetune_netGeom == 1:
                torch.save(net_geom.state_dict(), os.path.join(opt.checkpoints_dir, name, "netGeom_%02d.pth" % (epoch)))
            if opt.slow == 0:
                torch.save(gen_B.state_dict(), os.path.join(opt.checkpoints_dir, name, "netG_B_%02d.pth" % (epoch)))
                torch.save(disc_A.state_dict(), os.path.join(opt.checkpoints_dir, name, "netD_A_%02d.pth" % (epoch)))
                torch.save(disc_B.state_dict(), os.path.join(opt.checkpoints_dir, name, "netD_B_%02d.pth" % (epoch)))

        torch.save(gen_A.state_dict(), os.path.join(opt.checkpoints_dir, name, "netG_A_latest.pth"))
        torch.save(gen_B.state_dict(), os.path.join(opt.checkpoints_dir, name, "netG_B_latest.pth"))
        torch.save(disc_B.state_dict(), os.path.join(opt.checkpoints_dir, name, "netD_B_latest.pth"))
        torch.save(disc_A.state_dict(), os.path.join(opt.checkpoints_dir, name, "netD_A_latest.pth"))
        if opt.finetune_netGeom == 1:
            torch.save(net_geom.state_dict(), os.path.join(opt.checkpoints_dir, name, "netGeom_latest.pth"))

        # Saving images after each batch
        if not os.path.exists("generated_images"):
            os.makedirs("generated_images")

        from torchvision.utils import save_image
        exp_num = "exp10"
        save_image(fake_B.data, f"generated_images/{exp_num}/epoch_{epoch + 1}_fake_B.png", normalize=True)
        save_image(fake_A.data, f"generated_images/{exp_num}/epoch_{epoch + 1}_fake_A.png", normalize=True)
        save_image(rec_B.data, f"generated_images/{exp_num}/epoch_{epoch + 1}_rec_B.png", normalize=True)
        save_image(rec_A.data, f"generated_images/{exp_num}/epoch_{epoch + 1}_rec_A.png", normalize=True)
        fake_B_image = wandb.Image(fake_B.data, caption="fake_B")
        fake_A_image = wandb.Image(fake_A.data, caption="fake_A")
        rec_B_image = wandb.Image(rec_B.data, caption="rec_B")
        rec_A_image = wandb.Image(rec_A.data, caption="rec_A")
        wandb.log({
            "fake_B": fake_B_image,
            "fake_A": fake_A_image,
            "rec_B": rec_B_image,
            "rec_A": rec_A_image,
        })

        end_time = time.time()
        elapsed_time = round(end_time - start_time, 1)

    """
python train.py --name exp11 --full_color_dir examples/train/full_color --flat_color_dir examples/train/flat_color --no_flip --cuda --n_epochs 150 --decay_epoch 75 --batch_size 6 --wandb 0 --save_epoch_freq 1 --use_geom 0 --midas 0 --lr 0.0002 --use_clip 0 --cond_cycle 10.0 --use_sketch 1
    python train.py --name exp8 --dataroot examples/train/full_color --root2 examples/train/flat_color --no_flip --cuda --n_epochs 150 --decay_epoch 75 --batchSize 4 --wandb 1 --save_epoch_freq 1 --use_geom 0 --midas 0 --lr 6.5e-4 --use_clip 0 --cond_cycle 10.0 --use_sketch 1
    python train.py --name horses2zebras --dataroot examples/train/horses --root2 examples/train/zebras --no_flip --cuda --n_epochs 200 --decay_epoch 100 --batchSize 4 --wandb 1 --save_epoch_freq 1 --use_geom 0 --midas 0 --lr 1.6e-3 --use_clip 0 --cond_cycle 10.0
    
python train.py --name exp11 --full_color_dir examples/train/full_color --flat_color_dir examples/train/flat_color --no_flip --cuda --n_epochs 150 --decay_epoch 75 --batch_size 4 --wandb 0 --save_epoch_freq 1 --use_geom 0 --midas 0 --lr 6.5e-4 --use_clip 0 --cond_cycle 0.1 --cond_geom 10.0 --use_sketch 1

    python train.py --name vangogh2photo \
    --dataroot examples/train/vangogh \
    --depthroot examples/train/depthmaps \
    --root2 examples/train/photos \
    --no_flip --cuda --n_epochs 2 --decay_epoch 1 --batchSize 8 --wandb

    python train.py --name exp3 \
    --dataroot examples/train/full_color \
    --root2 examples/train/flat_color \
    --no_flip --cuda --n_epochs 100 --decay_epoch 50 --batchSize 8 --wandb --use_geom 0 --midas 0

    """
